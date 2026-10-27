# -*- coding: utf-8 -*-
"""
llm_agent.py -  LLM  (LM Studio Qwen 3B + Gemini Flash)
 Stop / Cancel 
"""
import json
import re
import asyncio
from typing import Dict, Any, List

from openai import AsyncOpenAI
from google import genai
from google.genai import types

from config import settings, state
from controller import controller
from cat_overlay import cat_hud

# ：，/， ask_user 
AGENT_SYSTEM_PROMPT = """ Windows 。
 JSON，：
{"thought": "", "action": "hotkey"|"type"|"press"|"wait"|"ask_user"|"done", "param": ""}

：
- "hotkey": ， "win+r"、"ctrl+l"、"ctrl+t"、"enter"
- "type": 
- "press": ， "enter"、"esc"、"tab"、"down"
- "wait": ， "3"
- "ask_user": 、/，，param 
- "done": ，param 

：
1. （ win+r  firefox  chrome）：
   {"thought": "", "action": "hotkey", "param": "win+r"}
2. ：
   {"thought": "", "action": "type", "param": "firefox https://www.youtube.com/results?search_query=wei+hong"}
3. ：
   {"thought": "", "action": "press", "param": "enter"}
4. ：
   {"thought": "", "action": "wait", "param": "4"}
5.  3 ：
    tab ， enter ！

 JSON，！
"""

class LLMAgentRunner:
    def __init__(self):
        # 1.  LM Studio (OpenAI Compatible) 
        self.lm_client = AsyncOpenAI(
            base_url=settings.LM_STUDIO_BASE_URL,
            api_key=settings.LM_STUDIO_API_KEY,
            timeout=30.0,
        )

        # 2.  Gemini 
        self.gemini_client = None
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
            try:
                self.gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            except Exception as e:
                state.add_log(f"⚠️ Gemini : {e}")

    def clean_json_response(self, text: str) -> Dict[str, Any]:
        """ LLM  JSON """
        text = text.strip()
        #  markdown 
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        
        #  { }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)

        try:
            return json.loads(text)
        except Exception:
            # 
            return {
                "thought": f": {text[:60]}...",
                "action": "done",
                "message": text
            }

    async def unload_all_models(self):
        """
        ， LM Studio REST API ，
         (VRAM)  (RAM)！
        """
        import urllib.request
        loop = asyncio.get_running_loop()

        def _do_unload():
            try:
                # 1. 
                req = urllib.request.Request("http://localhost:1234/api/v0/models")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    loaded = [m.get("id") for m in data.get("data", []) if m.get("state") == "loaded"]

                # 2.  v1 models/unload 
                unloaded_names = []
                for model_id in loaded:
                    if not model_id:
                        continue
                    body = json.dumps({"instance_id": model_id}).encode("utf-8")
                    unload_req = urllib.request.Request(
                        "http://localhost:1234/api/v1/models/unload",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    try:
                        with urllib.request.urlopen(unload_req, timeout=4.0):
                            unloaded_names.append(model_id)
                    except Exception as ue:
                        state.add_log(f"⚠️  {model_id} : {ue}")

                if unloaded_names:
                    state.add_log(f"🧹  LM Studio : {', '.join(unloaded_names)}")
                else:
                    state.add_log("🧹 LM Studio ，")
            except Exception as e:
                # LM Studio  REST 
                pass

        await loop.run_in_executor(None, _do_unload)

    async def plan_task_with_qwen(self, task_instruction: str) -> List[Dict[str, Any]]:
        """ LLM 、"""
        prompt = f"""You are an expert Windows desktop workflow engine.
Convert the user request into a complete, executable sequence of keyboard and navigation actions.

Allowed Actions:
- "open_url": Directly open any URL in the browser, e.g. "https://www.youtube.com/results?search_query=wei+hong"
- "focus_window": Bring a window to foreground, e.g. "chrome", "firefox", "edge"
- "hotkey": e.g. "ctrl+l", "ctrl+t", "ctrl+w", "enter"
- "type": search query, text, or exact URL
- "press": "enter", "tab", "down", "up", "space", "esc", "pagedown"
- "click": coordinate click (x: 0~1000, y: 0~1000)
- "wait": seconds to wait as string, e.g. "3"
- "ask_user": prompt the user with a question if unclear, e.g. asking which video or clarifying intent
- "done": completion message

Comprehensive Flow Rules:
1. Opening and searching websites (YouTube, Google, etc.):
   - Use "open_url" action to navigate directly and reliably:
     {{"thought": "Open YouTube search for wei hong", "action": "open_url", "param": "https://www.youtube.com/results?search_query=wei+hong"}}
2. Selecting and playing videos on YouTube search results:
   - Wait for the page to render: {{"thought": "Wait for search results to load", "action": "wait", "param": "4"}}
   - On the search page, the first item is often a channel header or filter chip.
   - To open the 3rd video:
     Use keyboard navigation or coordinate click:
     press "tab" -> wait "1" -> press "tab" -> wait "1" -> press "tab" -> wait "1" -> press "enter"
     OR scroll down: press "down" (several times) -> press "enter"
3. Needing user confirmation / unclear intent:
   - action "ask_user", param: "？"

Format MUST be a valid JSON array of action objects. Each object should have keys "thought", "action", and "param":
Example:
[
  {{"thought": "Open YouTube search directly in browser", "action": "open_url", "param": "https://www.youtube.com/results?search_query=wei+hong"}},
  {{"thought": "Wait for YouTube page to fully render", "action": "wait", "param": "4"}},
  {{"thought": "Navigate down to the video list", "action": "press", "param": "down"}},
  {{"thought": "Select 2nd video", "action": "press", "param": "down"}},
  {{"thought": "Select 3rd video", "action": "press", "param": "down"}},
  {{"thought": "Press Enter to play video", "action": "press", "param": "enter"}},
  {{"thought": "Wait for playback to start", "action": "wait", "param": "3"}},
  {{"thought": "Workflow completed", "action": "done", "param": "Successfully opened and started the 3rd video"}}
]

User Request: {task_instruction}
Output ONLY the JSON array:"""

        messages = [{"role": "user", "content": prompt}]
        state.add_log(f"🧠 [LM Studio]  (Flow)...")

        model_to_use = settings.QWEN_MODEL_NAME
        try:
            resp = await self.lm_client.chat.completions.create(
                model=model_to_use,
                messages=messages,
                temperature=0.1,
                max_tokens=600
            )
            content = resp.choices[0].message.content or ""
        except Exception:
            models_list = await self.lm_client.models.list()
            fallback_model = models_list.data[0].id if models_list.data else model_to_use
            resp = await self.lm_client.chat.completions.create(
                model=fallback_model,
                messages=messages,
                temperature=0.1,
                max_tokens=600
            )
            content = resp.choices[0].message.content or ""

        #  JSON 
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                raw_plan = json.loads(match.group(0))
                normalized_plan = []
                if isinstance(raw_plan, list):
                    for item in raw_plan:
                        if not isinstance(item, dict):
                            continue
                        act = str(item.get("action", "")).lower()
                        # 1. ，
                        if act in ("open", "launch"):
                            app = item.get("application") or item.get("app") or item.get("param", "")
                            url = item.get("url", "")
                            if url or "http" in str(app):
                                target = url or app
                                normalized_plan.append({"action": "open_url", "param": target, "thought": f": {target}"})
                            else:
                                normalized_plan.append({"action": "open_url", "param": "https://www.google.com", "thought": f": {app}"})
                            normalized_plan.append({"action": "wait", "param": "3", "thought": ""})

                        elif act == "search":
                            query = item.get("query") or item.get("param", "")
                            # ， YouTube 
                            yt_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
                            normalized_plan.append({"action": "open_url", "param": yt_url, "thought": f" YouTube : {query}"})
                            normalized_plan.append({"action": "wait", "param": "4", "thought": ""})

                        elif act in ("select", "choose", "click_video"):
                            target = str(item.get("item") or item.get("param", "")).lower()
                            #  (3)
                            down_times = 3
                            if "1st" in target or "first" in target or "1" in target:
                                down_times = 1
                            elif "2nd" in target or "second" in target or "2" in target:
                                down_times = 2
                            elif "3rd" in target or "third" in target or "3" in target:
                                down_times = 3
                            elif "4th" in target or "fourth" in target or "4" in target:
                                down_times = 4

                            # 
                            for idx in range(down_times):
                                normalized_plan.append({"action": "press", "param": "down", "thought": f" {idx+1} "})
                                normalized_plan.append({"action": "wait", "param": "0.5", "thought": ""})
                            normalized_plan.append({"action": "press", "param": "enter", "thought": f" {down_times} "})
                            normalized_plan.append({"action": "wait", "param": "3", "thought": ""})

                        else:
                            # 
                            param = item.get("param") or item.get(act, "")
                            thought = item.get("thought", f" {act}")
                            normalized_plan.append({"action": act, "param": param, "thought": thought})

                    #  done，
                    if normalized_plan and normalized_plan[-1]["action"] != "done":
                        normalized_plan.append({"action": "done", "param": "！", "thought": ""})

                    if normalized_plan:
                        return normalized_plan
            except Exception as e:
                pass
        return []

    async def call_lm_studio_qwen(self, task_instruction: str, history_logs: List[str]) -> Dict[str, Any]:
        """ LM Studio Qwen 3B """
        # ，
        if self._cached_plan is None:
            self._cached_plan = await self.plan_task_with_qwen(task_instruction)
            self._plan_step_idx = 0

        # ，
        if self._cached_plan and self._plan_step_idx < len(self._cached_plan):
            step_action = self._cached_plan[self._plan_step_idx]
            self._plan_step_idx += 1
            return step_action

        # 
        user_prompt = f""": {task_instruction}
:
{chr(10).join(history_logs) if history_logs else '(， 1 )'}

， JSON："""

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        state.add_log(f"🧠 [LM Studio Qwen 3B] ...")
        
        # 
        model_to_use = settings.QWEN_MODEL_NAME
        try:
            resp = await self.lm_client.chat.completions.create(
                model=model_to_use,
                messages=messages,
                temperature=0.1,
                max_tokens=200
            )
            content = resp.choices[0].message.content or ""
            return self.clean_json_response(content)
        except Exception as e:
            models_list = await self.lm_client.models.list()
            if models_list.data:
                fallback_model = models_list.data[0].id
                resp = await self.lm_client.chat.completions.create(
                    model=fallback_model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=200
                )
                content = resp.choices[0].message.content or ""
                return self.clean_json_response(content)
            raise e

    async def call_gemini_vision(self, task_instruction: str, history_logs: List[str]) -> Dict[str, Any]:
        """ Gemini Flash () """
        if not self.gemini_client:
            raise RuntimeError("Gemini API Key ， LM Studio ！")

        state.add_log("📷  Gemini Flash ...")
        # 
        img_bytes = controller.capture_screen_bytes(max_width=1024, quality=70)
        
        prompt = f"""
{AGENT_SYSTEM_PROMPT}

：{task_instruction}
。
：
{chr(10).join(history_logs[-4:])}

，(0~1000)， JSON：
"""
        #  Gemini 
        loop = asyncio.get_running_loop()
        def _call_gemini():
            return self.gemini_client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                ]
            )

        resp = await loop.run_in_executor(None, _call_gemini)
        return self.clean_json_response(resp.text or "")


    async def call_custom_openai_model(self, task_instruction: str, history_logs: List[str], model_id: str) -> Dict[str, Any]:
        """ OpenAI  ( OpenRouter, Groq, SiliconFlow, Cerebras )"""
        cfg = state.custom_models.get(model_id, {})
        base_url = cfg.get("base_url", "https://api.openai.com/v1")
        api_key = cfg.get("api_key") or "dummy-key"
        actual_model = cfg.get("model", "gpt-3.5-turbo")

        state.add_log(f"🌐  [{cfg.get('name', model_id)}]: {actual_model}...")

        custom_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=35.0,
        )

        prompt = f"""{AGENT_SYSTEM_PROMPT}

：{task_instruction}
：
{chr(10).join(history_logs[-4:])}

 JSON："""

        resp = await custom_client.chat.completions.create(
            model=actual_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
        content = resp.choices[0].message.content or ""
        return self.clean_json_response(content)

    async def run_task(self, instruction: str) -> str:
        """
        AI ，。
        """
        # 
        state.stop_requested = False
        state.is_agent_busy = True
        self._cached_plan = None
        self._plan_step_idx = 0
        state.add_log(f"🚀 : '{instruction}' (: {state.active_model})")
        cat_hud.update_status("working", f": {instruction[:20]}")

        step_count = 0
        history_actions = []

        try:
            while step_count < settings.MAX_AGENT_STEPS:
                step_count += 1

                # 
                if state.stop_requested:
                    raise asyncio.CancelledError("！")

                state.add_log(f"⏳  [{step_count}/{settings.MAX_AGENT_STEPS}] ...")

                # 1. ：
                try:
                    if state.active_model == "gemini":
                        action_plan = await self.call_gemini_vision(instruction, history_actions)
                    elif state.active_model in state.custom_models:
                        action_plan = await self.call_custom_openai_model(instruction, history_actions, state.active_model)
                    else:
                        action_plan = await self.call_lm_studio_qwen(instruction, history_actions)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    state.add_log(f"❌ : {e}")
                    #  LM Studio ，
                    if "1234" in str(e):
                        return f" LM Studio ， LM Studio  Local Server  http://localhost:1234: {e}"
                    return f": {e}"

                # 
                if state.stop_requested:
                    raise asyncio.CancelledError("！")

                action_type = action_plan.get("action", "").lower()
                thought = action_plan.get("thought", "")

                # 2.  (ask_user)
                if action_type == "ask_user":
                    question_text = str(action_plan.get("param") or thought or ":")
                    state.add_log(f"❓ LLM : {question_text}")
                    cat_hud.update_status("working", f"Awaiting input: {question_text[:20]}...")
                    cat_hud.ask_user_dialog(question_text)
                    state.ask_user(question_text)
                    
                    user_reply = await state.wait_for_user_answer(timeout=60.0)
                    history_actions.append(f"Step {step_count} [Ask User]: '{question_text}' -> User reply: '{user_reply}'")
                    self._cached_plan = None
                    self._plan_step_idx = 0
                    instruction = f"{instruction} (User clarification: {user_reply})"
                    await asyncio.sleep(0.5)
                    continue

                # 3. Check if completed
                if action_type == "done":
                    done_msg = action_plan.get("message", "Task successfully completed!")
                    state.add_log(f"🎉 Task Completed: {done_msg}")
                    cat_hud.update_status("done", "Task successfully completed!")
                    cat_hud.show_toast("🎉 Task Completed", f"{instruction[:30]} completed!", "completed")
                    state.notify_user("completed", "🎉 Task Completed", f"{instruction} completed!")
                    return f"✅ Task completed: {done_msg}"

                # 4. Execute action
                try:
                    loop = asyncio.get_running_loop()
                    exec_result = await loop.run_in_executor(
                        None, controller.execute_action, action_plan
                    )
                    history_actions.append(f"Step {step_count} [{action_type}]: {exec_result}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    state.add_log(f"⚠️ Action execution warning: {e}")
                    history_actions.append(f"Step {step_count} error: {e}")

                await asyncio.sleep(1.0)

            final_msg = f"Reached maximum allowed steps ({settings.MAX_AGENT_STEPS} steps), task ended."
            state.add_log(f"⏹️ {final_msg}")
            cat_hud.update_status("idle", "Ready & idling")
            cat_hud.show_toast("⏹️ Task Finished", final_msg, "completed")
            state.notify_user("completed", "⏹️ Task Finished", final_msg)
            return final_msg

        except asyncio.CancelledError:
            state.add_log("🛑 Abort signal received: execution loop stopped!")
            cat_hud.update_status("idle", "Interrupted")
            cat_hud.show_toast("🛑 Automation Stopped", "User aborted the current operation.", "stopped")
            state.notify_user("stopped", "🛑 Automation Stopped", "Task operation aborted.")
            return "🛑 Task successfully aborted!"
        finally:
            state.is_agent_busy = False
            state.current_task = None
            if not state.keep_alive_enabled:
                cat_hud.update_status("idle", "Ready & idling")
            # ， LM Studio ！
            try:
                await self.unload_all_models()
            except Exception as ex:
                state.add_log(f"⚠️ : {ex}")


agent_runner = LLMAgentRunner()
