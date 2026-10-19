import ast
import json
import os
import re
import subprocess
from pathlib import Path

#readline修补，可以在终端调出命令和移动，parse_and_binf是设置utf-8友好模式
try:
    import readline
    # #143 UTF-8 backspace fix for macOS libedit
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

#从.env文件中加载环境变量，override=True表示覆盖现有的环境变量
load_dotenv(override=True)
#中转网关兼容处理
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
#指定api端点
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

WORKDIR = Path.cwd()

#从环境变量中读取模型ID
MODEL = os.environ["MODEL_ID"]

#系统提示
#模型在哪个目录，运行环境等

ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax, and prefer dedicated file tools for file operations"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go."
)


#真正动手的工具实现
def run_bash(command:str)->str:
    #放在之前权限判断处审批
    # dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

    # if any(d in command for d in dangerous):
    #     return "Error: Dangerous command detected. Aborting."
    try:
        r = subprocess.run(
            command,shell=True,cwd=WORKDIR,
            capture_output=True,text=True,
            errors="replace",timeout = 120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(No output.没有输出)"
    except subprocess.TimeoutExpired:
        return "Error:Timeout(120s)"
    # except (FileNotFoundError, OSError) as e:
    #     return f"Error: {str(e)}"


#读取文件
def run_read(path:str,limit:int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines)-limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error : {e}"

#写文件
def run_write(path:str,content:str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content,encoding="utf-8")
        return f"Wrote {len(content)} bytes to {file_path}"
    except Exception as e:
        return f"Error : {e}"

#修改文件
def run_edit(path:str,old_text:str,new_text:str)-> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: '{old_text}' 不在 {file_path}"
        file_path.write_text(text.replace(old_text,new_text,1),encoding="utf-8")
        return f"修改了{path}"
    except Exception as e:
        return f"Error : {e}"

#模式匹配找文件
def run_glob(pattern:str)->str:
    import glob as g
    try:
        match = sorted({
            match for match in g.glob(
                pattern,root_dir = WORKDIR,recursive=True
            ) if (WORKDIR/match).resolve().is_relative_to(WORKDIR)
        })
        shown = match[:200]
        if len(match) > 200:
            shown.append("...(more matches omitted,narrow the pattern)")
        return "\n".join(shown) if shown else "(No matches.没有匹配)"
    except Exception as e:
        return f"Error : {e}"

#todomanager-状态机+校验器+渲染器
class TodoManager:
    def __init__(self):
        self.items: list[dict] = []

    def update(self,todos:list | str)->str:
        #解析输入,容忍字符串
        if isinstance(todos,str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError,ValueError) as e:
                    raise ValueError("todos 必须是 list ot Json array string") from e
        #校验
        if not isinstance(todos,list):
            raise ValueError("todos 必须是 list")
        if len(todos) > 20:
            raise ValueError("最多20个todo")
        validated = []
        in_progress_count = 0
        for index,todo in enumerate(todos):
            if not isinstance(todo,dict):
                raise ValueError(f"todos[{index}] 必须是一个对象")

            content = str(todo.get("content","")).strip()
            status = str(todo.get("status","pending")).lower()
            if not content:
                raise ValueError(f"todos[{index}] content不能为空")
            if status not in ("pending","in_progress","completed"):
                raise ValueError(f"todos[{index}] 无效状态'{status}")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content":content,"status":status})
        if in_progress_count > 1:
            raise ValueError("同时只能有一个todo被执行")

        self.items = validated
        return self.render()

#渲染，给人看+给模型看 
    def render(self)->str:
        if not self.item:
            return "NO todos"

        lines = []
        for todo in self.items:
            marker = {
                "pending":"[ ]",
                "in_progress":"[>]",
                "completed":"[x]",
            }[todo["status"]]
            lines.append(f"{marker} {todo['content']}")

        done = sum(todo["status"]=="completed" for todo in self.items)
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)
    #输出格式：
    # [ ] 读取现有代码
    # [>] 重构 run_bash
    # [x] 写单元测试

    # (1/3 completed)
TODO = TodoManager()

#工具封装层
def run_todo_write(todos:list | str) -> str:
    try:
        output = TODO.update(todos)
    except ValueError as e:
        return f"Error:{e}"
    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output

#工具定义
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    # s05: new tool
    {"name": "todo_write", "description": "Create and manage a task list for your current coding session.",
     "input_schema": {"type": "object", "properties": {"todos": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {"content": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["content", "status"]}}}, "required": ["todos"]}},
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "todo_write":run_todo_write,
}

#hoook系统
HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}

def register_hook(event:str,callback):
    HOOKS[event].append(callback)

def trigger_hook(event:str,*args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None

#gate1，硬拒绝名单
DENY_KIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda"]
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

# def check_deny_list(command:str)->str | None:
#     for pattern in DENY_KIST:
#         if pattern in command:
#             return f"操作 ({pattern})在拒绝名单，不能执行"


# #规则匹配
# DESTRUCTIVE_COMMAND_WORD = re.compile(
#     r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
# )

def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))

def permission_hook(block):
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_KIST:
            if pattern in command:
                print(f"\n\033[31m[blocked] 操作 ({pattern})在拒绝名单，不能执行\033[0m")
                return "Permission denied by deny list"
        if contains_destructive_command(command) or any(
            kw in command for kw in DESTRUCTIVE
            ):
            print(f"\n\033[33m[permission] 潜在破坏性命令\033[0m")
            print(f"   Tool: {block.name}({block.input})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "用户拒绝了该命令"
        if block.name in ["read_file", "write_file", "edit_file"]:
            path = block.input.get("path", "")
            if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
                print(f"\n\033[31m[blocked] 这个文件位置不能写\033[0m")
                print(f"   Tool: {block.name}({block.input})")
                choice = input("   Allow? [y/N] ").strip().lower()
                if choice not in ("y", "yes"):
                    return "用户拒绝了该命令"
        return None

#preToolUse的审计日志
def log_hook(block):
    args_preview = str(list(block.input.values())[:2])[:60]
    print(f"\033[90m[HOOK] {block.name}({args_preview})\033[0m")
    return None

#PostToolUse输出审查
def large_output_hook(block, output):
    if len(output) > 50000:
         print(f"\033[33m[HOOK] 输出太长了 {block.name}: {len(str(output))} chars\033[0m")
    return output

#UserPromptSubmit的审查
def context_inject_hook(query:str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None

#Stop
def summary_hook(messages:list):
    tool_count = sum(1 for m in messages
                     for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                     if isinstance(b, dict) and b.get("type") == "tool_result")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None

register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


#核心循环
def agent_loop(messages:list):
    #增加本地状态
    rounds_since_todo = 0
    while True:
        response = client.messages.create(
            model = MODEL,system = SYSTEM,messages = messages,
            tools = TOOLS,max_tokens = 8000,
        )

        messages.append({"role":"assistant","content":response.content})

        # tool_calls = {
        #     block for block in response.content if block.type == "tool_use"
        # }
        tool_calls = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                tool_calls.append(block)

        if not tool_calls:
            force = trigger_hook("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return
        results = []
        used_todo = False

        for block in tool_calls:
            # print(f"\033[33m${block.name}\033[0m")
            blocked = trigger_hook("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(blocked)})
                continue
           

            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
            except Exception as e:
                output = f"Error: {e}"

            trigger_hook("PostToolUse", block, output)

            if block.name == "todo_write":
                used_todo = True

            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": str(output)})

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            results.append({"type": "text",
                            "text": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\001\033[36m\002s05，增加规划 >> \001\033[0m\002")
        except (EOFError,KeyboardInterrupt):
            break
        if query.strip().lower() in ("q","exit",""):
            break

        trigger_hook("UserPromptSubmit", query)
        history.append({"role":"user","content":query})
        agent_loop(history)

        # response_content = history[-1]["content"]
        for block in history[-1]["content"]:
            if getattr(block,"type",None) == "text":
                print(block.text)
        print()