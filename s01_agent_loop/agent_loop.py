import os
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
#从环境变量中读取模型ID
MODEL = os.environ["MODEL_ID"]

#系统提示
#模型在哪个目录，运行环境等

ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)
SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. Environment: {ENVIRONMENT_PROMPT}. "
    "Use bash to solve tasks. Act, don't explain."
)

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
}]

#真正动手的工具实现
def run_bash(command:str)->str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

    if any(d in command for d in dangerous):
        return "Error: Dangerous command detected. Aborting."
    try:
        r = subprocess.run(
            command,shell=True,cwd=os.getcwd(),
            capture_output=True,text=True,
            errors="replace",timeout = 120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(No output.没有输出)"
    except subprocess.TimeoutExpired:
        return "Error:Timeout(120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {str(e)}"

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
            output = run_bash(block.input["command"])
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
            query = input("\001\033[36m\002s01 >> \001\033[0m\002")
        except (EOFError,KeyboardInterrupt):
            break
        if query.strip().lower() in ("q","exit",""):
            break

        history.append({"role":"user","content":query})
        agent_loop(history)

        response_content = history[-1]["content"]
        if isinstance(response_content,list):
            for block in response_content:
                if getattr(block,"type",None) == "text":
                    print(block.text)

        print()