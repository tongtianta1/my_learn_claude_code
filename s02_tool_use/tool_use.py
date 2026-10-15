import os
from os import path
from pathlib import Path
import subprocess

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
    "Use bash to solve tasks. Act, don't explain."
)


#真正动手的工具实现
def run_bash(command:str)->str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

    if any(d in command for d in dangerous):
        return "Error: Dangerous command detected. Aborting."
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
    except (FileNotFoundError, OSError) as e:
        return f"Error: {str(e)}"

#s02修改，确保文件修改路径安全
def safe_path(p:str) -> path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Unsafe path: {p}")
    return path

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

#工具定义
TOOLS = [{
    "name":"bash",
    "description":"Run a shell conmand,",
    "input_schema":{
        "type":"object",
        "properties":{
            "command":{
                "type":"string",
            }
        },
        "required":["command"],
    }
},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},

]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}



#核心循环
def agent_loop(messages:list):
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
            return
        result = []
        for block in tool_calls:
            print(f"\033[33m${block.input['command']}\033[0m")
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Error: 没有 '{block.name}'工具"
            print(output[:200])
            result.append({
                "type":"tool_result",
                "tool_use_id":block.id,
                "content":output
            })

        messages.append({"role":"user","content":result})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\001\033[36m\002s02，比01多了四个工具 >> \001\033[0m\002")
        except (EOFError,KeyboardInterrupt):
            break
        if query.strip().lower() in ("q","exit",""):
            break

        history.append({"role":"user","content":query})
        agent_loop(history)

        response_content = history[-1]["content"]
        for block in history[-1]["content"]:
            if getattr(block,"type",None) == "text":
                print(block.text)
        print()