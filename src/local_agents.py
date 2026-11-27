"""Use installed agent CLIs with their own account sign-in."""

from contextlib import ExitStack, contextmanager
from collections import deque
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time


CLIENT = {"name": "screen_reader", "title": "SurMuy", "version": "1.0"}
AUTOMATIC_MODEL = "Automatic"


def codex_environment():
    env = os.environ.copy()
    if env.get("USERPROFILE") and "CODEX_HOME" not in env:
        env["CODEX_HOME"] = str(Path(env["USERPROFILE"]) / ".codex")
    return env


def codex_executable():
    return find_executable("codex.exe", (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/OpenAI/Codex/bin/codex.exe",
    ))


def gemini_executable():
    return find_executable("agy.exe", (
        Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".gemini/bin/agy.exe",
    ))


def find_executable(name, locations):
    executable = shutil.which(name) or next((str(path) for path in locations if path.is_file()), None)
    if not executable:
        raise RuntimeError(f"The local CLI ({name}) was not found.")
    return executable


class AgentAuthenticationRequired(RuntimeError):
    pass


class AgentRPC:
    def __init__(self, process):
        self.process = process
        self.messages = queue.Queue()
        self.request_id = 0
        self.answer = []
        threading.Thread(target=self.read_messages, daemon=True).start()

    def read_messages(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        finally:
            self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def call(self, method, params, timeout=20, cancel=None, on_event=None):
        self.request_id += 1
        request_id = self.request_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.receive(deadline, method, cancel)
            if "method" in message:
                self.on_agent_message(message)
                if on_event is not None:
                    on_event(message)
            elif message.get("id") == request_id:
                if "error" in message:
                    details = message["error"]
                    if details.get("code") == -32000 and "auth" in details.get("message", "").lower():
                        raise AgentAuthenticationRequired("Antigravity requires a Google sign-in.")
                    raise RuntimeError(details.get("message", "The local agent rejected the request."))
                return message["result"]

    def receive(self, deadline, method, cancel):
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Connection cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"The local agent timed out during {method}.")
            try:
                message = self.messages.get(timeout=min(0.25, remaining) if cancel is not None else remaining)
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError("The local agent exited before it replied.")
            return message

    def on_agent_message(self, message):
        method = message["method"]
        if "id" in message:
            if method == "session/request_permission":
                self.send({"id": message["id"], "result": {"outcome": {"outcome": "cancelled"}}})
            else:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Method unavailable"}})
        elif method == "session/update":
            update = message.get("params", {}).get("update", {})
            content = update.get("content", {})
            if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                self.answer.append(content["text"])


@contextmanager
def agent_process(command, folder, env=None):
    with subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace",
                          creationflags=subprocess.CREATE_NO_WINDOW) as process:
        try:
            yield AgentRPC(process)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def list_codex_models():
    env = codex_environment()
    try:
        return query_codex_models(env)
    except (RuntimeError, OSError):
        # Current Codex builds also expose their bundled catalog without account state.
        with tempfile.TemporaryDirectory(prefix="screen-reader-codex-catalog-") as folder:
            return query_codex_models({**env, "CODEX_HOME": folder})


def query_codex_models(env):
    with tempfile.TemporaryDirectory(prefix="screen-reader-codex-") as folder:
        with agent_process([codex_executable(), "app-server", "--listen", "stdio://"],
                           folder, env) as agent:
            agent.call("initialize", {"clientInfo": CLIENT})
            agent.send({"method": "initialized"})
            models = []
            cursor = None
            while True:
                params = {"limit": 100, "includeHidden": False}
                if cursor:
                    params["cursor"] = cursor
                result = agent.call("model/list", params)
                models.extend(entry["model"] for entry in result.get("data", []) if entry.get("model"))
                cursor = result.get("nextCursor")
                if not cursor:
                    return list(dict.fromkeys(models))


def connect_codex(cancel=None):
    command = [codex_executable(), "login"]
    env = codex_environment()
    options = {"env": env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "creationflags": subprocess.CREATE_NO_WINDOW}
    if subprocess.run([*command, "status"], timeout=10, **options).returncode == 0:
        return
    deadline = time.monotonic() + 180
    with subprocess.Popen(command, **options) as process:
        while process.poll() is None:
            if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
                process.terminate()
                raise RuntimeError("ChatGPT sign-in was cancelled or timed out.")
            try:
                process.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError("Codex could not sign in. Check its sign-in in your terminal.")


@contextmanager
def gemini_agent(*, cancel=None):
    with tempfile.TemporaryDirectory(prefix="screen-reader-gemini-") as folder:
        command = [gemini_executable(), "acp", "--sandbox", "--disable-telemetry",
                   "--log-file", str(Path(folder) / "agent.log")]
        with agent_process(command, folder) as agent:
            capabilities = agent.call("initialize", {
                "protocolVersion": 1, "clientInfo": CLIENT, "clientCapabilities": {},
            }, cancel=cancel)
            yield agent, capabilities, folder


def start_gemini_session(agent, capabilities, folder, *, connect=False, cancel=None):
    params = {"cwd": folder, "mcpServers": []}
    try:
        return agent.call("session/new", params, cancel=cancel)
    except AgentAuthenticationRequired:
        if not connect:
            raise AgentAuthenticationRequired(
                "Antigravity could not use a saved sign-in. Choose Sign in with Google in AI settings."
            ) from None
        methods = {item.get("id") for item in capabilities.get("authMethods", [])}
        if "oauth-personal" not in methods:
            raise RuntimeError("This Antigravity version does not offer Google sign-in through the app.")
        agent.call("authenticate", {"methodId": "oauth-personal"}, timeout=180, cancel=cancel)
        return agent.call("session/new", params, cancel=cancel)


@contextmanager
def gemini_session(*, connect=False, cancel=None):
    with gemini_agent(cancel=cancel) as (agent, capabilities, folder):
        session = start_gemini_session(agent, capabilities, folder, connect=connect, cancel=cancel)
        yield agent, capabilities, session


def gemini_model_option(session):
    return next((item for item in session.get("configOptions", [])
                 if item.get("category") == "model" or item.get("id") == "model"), None)


def list_gemini_account_models(*, connect=False, cancel=None):
    with gemini_session(connect=connect, cancel=cancel) as (_, _, session):
        option = gemini_model_option(session)
        if option:
            return [entry["value"] for group in option.get("options", [])
                    for entry in group.get("options", [group]) if entry.get("value")]
        return [entry["modelId"] for entry in session.get("models", {}).get("availableModels", [])
                if entry.get("modelId")]


def set_gemini_model(agent, session, model, cancel=None):
    if not model or model == AUTOMATIC_MODEL:
        return
    session_id = session["sessionId"]
    option = gemini_model_option(session)
    if option:
        agent.call("session/set_config_option", {
            "sessionId": session_id, "configId": option["id"], "value": model,
        }, cancel=cancel)
    else:
        agent.call("session/set_model", {"sessionId": session_id, "modelId": model}, cancel=cancel)


def gemini_prompt(agent, capabilities, session, text_prompt, image_data, cancel=None):
    prompt = [{"type": "text", "text": text_prompt}]
    if image_data:
        if not capabilities.get("agentCapabilities", {}).get("promptCapabilities", {}).get("image"):
            raise RuntimeError("This Antigravity version reports that image prompts are unavailable.")
        prompt.append({"type": "image", "mimeType": "image/png", "data": image_data})
    agent.answer.clear()
    agent.call("session/prompt", {"sessionId": session["sessionId"], "prompt": prompt},
               timeout=120, cancel=cancel)
    return "".join(agent.answer).strip()


def ask_gemini_account(text_prompt, image_data=None, *, model=None):
    with gemini_session() as (agent, capabilities, session):
        set_gemini_model(agent, session, model)
        return gemini_prompt(agent, capabilities, session, text_prompt, image_data)


class LocalChat:
    """Keep one signed-in process and reuse its current conversation when possible."""

    def __init__(self, open_agent, open_thread, send_turn):
        self.open_agent = open_agent
        self.open_thread = open_thread
        self.send_turn = send_turn
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.stack = ExitStack()
        self.server = None
        self.session = None
        self.session_key = None
        self.next_turn = 0

    def ask(self, *, conversation_id, turn, model, initial_prompt, follow_up, get_image):
        with self.lock:
            if self.cancel.is_set():
                raise RuntimeError("The agent connection was closed.")
            try:
                if self.server is not None and self.server[0].process.poll() is not None:
                    self.reset_agent()
                if self.server is None:
                    self.server = self.stack.enter_context(self.open_agent(cancel=self.cancel))
                agent, capabilities, folder = self.server
                session_key = (conversation_id, model or AUTOMATIC_MODEL)
                new_session = self.session_key != session_key or self.next_turn != turn
                if new_session:
                    self.session = self.open_thread(agent, capabilities, folder, model, self.cancel)
                    self.session_key = session_key
                answer = self.send_turn(
                    agent, capabilities, self.session,
                    initial_prompt if new_session else follow_up,
                    get_image() if new_session else None, self.cancel,
                )
                self.next_turn = turn + 1
                return answer
            except Exception:
                self.reset_agent()
                raise

    def reset_agent(self):
        self.server = None
        self.session = None
        self.session_key = None
        self.stack.close()

    def close(self):
        self.cancel.set()
        with self.lock:
            self.reset_agent()


def open_gemini_thread(agent, capabilities, folder, model, cancel):
    session = start_gemini_session(agent, capabilities, folder, cancel=cancel)
    set_gemini_model(agent, session, model, cancel)
    return session


def GeminiChat():
    return LocalChat(gemini_agent, open_gemini_thread, gemini_prompt)


class CodexUnavailable(RuntimeError):
    """The app-server could not prepare a thread before sending the prompt."""


@contextmanager
def codex_agent(*, cancel):
    with tempfile.TemporaryDirectory(prefix="cheat-master-codex-") as folder, ExitStack() as stack:
        command = [codex_executable(), "app-server", "--listen", "stdio://",
                   "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
                   "-c", 'web_search="disabled"', "-c", "mcp_servers={}"]
        try:
            agent = stack.enter_context(agent_process(command, folder, codex_environment()))
            capabilities = agent.call("initialize", {"clientInfo": CLIENT}, cancel=cancel)
            agent.send({"method": "initialized"})
        except (RuntimeError, OSError) as problem:
            if cancel.is_set():
                raise
            raise CodexUnavailable(str(problem)) from problem
        yield agent, capabilities, folder


def open_codex_thread(agent, _capabilities, folder, model, cancel):
    params = {"cwd": folder, "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True}
    if model and model != AUTOMATIC_MODEL:
        params["model"] = model
    try:
        return agent.call("thread/start", params, cancel=cancel)["thread"]
    except (RuntimeError, OSError) as problem:
        if cancel.is_set():
            raise
        raise CodexUnavailable(str(problem)) from problem


def codex_prompt(agent, _capabilities, thread, text_prompt, image_data, cancel):
    thread_id = thread["id"]
    content = [{"type": "text", "text": text_prompt}]
    if image_data:
        content.append({"type": "image", "url": f"data:image/png;base64,{image_data}"})
    pending = deque()
    result = agent.call("turn/start", {"threadId": thread_id, "input": content},
                        cancel=cancel, on_event=pending.append)
    turn_id = result["turn"]["id"]
    deadline = time.monotonic() + 120
    deltas = {}
    answer = ""
    while True:
        if pending:
            event = pending.popleft()
        else:
            event = agent.receive(deadline, "turn/completed", cancel)
            if "method" in event:
                agent.on_agent_message(event)
        params = event.get("params", {})
        if params.get("threadId") != thread_id:
            continue
        method = event.get("method")
        if method == "turn/completed" and params["turn"]["id"] == turn_id:
            turn = params["turn"]
            if turn["status"] != "completed":
                raise RuntimeError((turn.get("error") or {}).get("message", "Codex did not complete the turn."))
            return (answer or "".join(deltas.get(next(reversed(deltas), None), ()))).strip()
        if params.get("turnId") != turn_id:
            continue
        if method == "item/agentMessage/delta":
            deltas.setdefault(params["itemId"], []).append(params["delta"])
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                answer = item.get("text", answer)


def CodexChat():
    return LocalChat(codex_agent, open_codex_thread, codex_prompt)
