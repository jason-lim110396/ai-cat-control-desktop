# -*- coding: utf-8 -*-
"""
llm_agent.py - 双引擎 LLM 规划与自动化执行器 (LM Studio Qwen 3B + Gemini Flash)
支持实时 Stop / Cancel 强掐断
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

# 系统规划提示词：严格按部就班执行，遇到不明确/需要用户确认或选特定视频时，可发起 ask_user 询问
AGENT_SYSTEM_PROMPT = """你是一个 Windows 桌面纯键盘鼠标自动化执行助手。
你每次只能输出且必须输出单行标准 JSON，格式为：
{"thought": "当前要做什么", "action": "hotkey"|"type"|"press"|"wait"|"ask_user"|"done", "param": "参数"}

动作定义：
- "hotkey": 组合键，如 "win+r"、"ctrl+l"、"ctrl+t"、"enter"
- "type": 输入文本或网址
- "press": 单键，如 "enter"、"esc"、"tab"、"down"
- "wait": 等待秒数，如 "3"
- "ask_user": 当你不明白用户意图、或者需要用户确认目标视频/选项时，向用户提问输入，param 为提问文字
- "done": 完成任务，param 为完成提示

标准流程规范：
1. 启动浏览器（更稳妥的方式是 win+r 输入 firefox 或 chrome）：
   {"thought": "打开运行对话框", "action": "hotkey", "param": "win+r"}
2. 输入浏览器并启动：
   {"thought": "输入浏览器并访问目标网址", "action": "type", "param": "firefox https://www.youtube.com/results?search_query=wei+hong"}
3. 回车打开：
   {"thought": "按下回车打开页面", "action": "press", "param": "enter"}
4. 等待加载：
   {"thought": "等待网页加载", "action": "wait", "param": "4"}
5. 寻找并打开第 3 个视频：
   可以通过连续 tab 键或连续向下按键滚动定位到视频，然后按 enter 打开！

只返回合法的单行 JSON，禁止任何解释！
"""

class LLMAgentRunner:
    def __init__(self):
        # 1. 初始化 LM Studio (OpenAI Compatible) 客户端
        self.lm_client = AsyncOpenAI(
            base_url=settings.LM_STUDIO_BASE_URL,
            api_key=settings.LM_STUDIO_API_KEY,
            timeout=30.0,
        )

        # 2. 初始化 Gemini 客户端
        self.gemini_client = None
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
            try:
                self.gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            except Exception as e:
                state.add_log(f"⚠️ Gemini 初始化提示: {e}")

    def clean_json_response(self, text: str) -> Dict[str, Any]:
        """清洗并解析 LLM 返回的 JSON 格式"""
        text = text.strip()
        # 去除 markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        
        # 尝试寻找第一对 { }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)

        try:
            return json.loads(text)
        except Exception:
            # 容错处理
            return {
                "thought": f"直接回答: {text[:60]}...",
                "action": "done",
                "message": text
            }

    async def unload_all_models(self):
        """
        自动化任务完成或中断后，调用 LM Studio REST API 卸载全部已加载的模型实例，
        彻底释放显存 (VRAM) 与系统内存 (RAM)！
        """
        import urllib.request
        loop = asyncio.get_running_loop()

        def _do_unload():
            try:
                # 1. 查询当前加载的模型列表
                req = urllib.request.Request("http://localhost:1234/api/v0/models")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    loaded = [m.get("id") for m in data.get("data", []) if m.get("state") == "loaded"]

                # 2. 逐一调用 v1 models/unload 卸载
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
                        state.add_log(f"⚠️ 释放模型 {model_id} 异常: {ue}")

                if unloaded_names:
                    state.add_log(f"🧹 已释放 LM Studio 显存与模型: {', '.join(unloaded_names)}")
                else:
                    state.add_log("🧹 LM Studio 模型已处于释放状态，显存清洁")
            except Exception as e:
                # LM Studio 可能未开启 REST 或处于离线状态
                pass

        await loop.run_in_executor(None, _do_unload)

    async def plan_task_with_qwen(self, task_instruction: str) -> List[Dict[str, Any]]:
        """使用本地 LLM 将用户的多步骤复合任务拆解为连贯、可执行的完整按键与导航流"""
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
   - action "ask_user", param: "请问你想播放具体哪部视频？"

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
        state.add_log(f"🧠 [LM Studio] 正在分析并编排全套工作流 (Flow)...")

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

        # 清洗并解析 JSON 数组
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
                        # 1. 如果模型输出了高层抽象操作，智能编译为低层执行动作
                        if act in ("open", "launch"):
                            app = item.get("application") or item.get("app") or item.get("param", "")
                            url = item.get("url", "")
                            if url or "http" in str(app):
                                target = url or app
                                normalized_plan.append({"action": "open_url", "param": target, "thought": f"打开网址: {target}"})
                            else:
                                normalized_plan.append({"action": "open_url", "param": "https://www.google.com", "thought": f"启动浏览器: {app}"})
                            normalized_plan.append({"action": "wait", "param": "3", "thought": "等待浏览器窗口加载"})

                        elif act == "search":
                            query = item.get("query") or item.get("param", "")
                            # 如果是找视频或频道，优先直接导向 YouTube 搜索
                            yt_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
                            normalized_plan.append({"action": "open_url", "param": yt_url, "thought": f"在 YouTube 搜索: {query}"})
                            normalized_plan.append({"action": "wait", "param": "4", "thought": "等待搜索结果渲染完成"})

                        elif act in ("select", "choose", "click_video"):
                            target = str(item.get("item") or item.get("param", "")).lower()
                            # 提取第几部视频 (默认第3部)
                            down_times = 3
                            if "1st" in target or "first" in target or "第1" in target:
                                down_times = 1
                            elif "2nd" in target or "second" in target or "第2" in target:
                                down_times = 2
                            elif "3rd" in target or "third" in target or "第3" in target:
                                down_times = 3
                            elif "4th" in target or "fourth" in target or "第4" in target:
                                down_times = 4

                            # 下移定位到指定视频条目
                            for idx in range(down_times):
                                normalized_plan.append({"action": "press", "param": "down", "thought": f"向下移动光标选择第 {idx+1} 个项目"})
                                normalized_plan.append({"action": "wait", "param": "0.5", "thought": "等待光标聚焦"})
                            normalized_plan.append({"action": "press", "param": "enter", "thought": f"按回车打开第 {down_times} 个视频"})
                            normalized_plan.append({"action": "wait", "param": "3", "thought": "等待视频缓冲播放"})

                        else:
                            # 标准底层动作
                            param = item.get("param") or item.get(act, "")
                            thought = item.get("thought", f"执行 {act}")
                            normalized_plan.append({"action": act, "param": param, "thought": thought})

                    # 如果最后没有 done，自动附加完成动作
                    if normalized_plan and normalized_plan[-1]["action"] != "done":
                        normalized_plan.append({"action": "done", "param": "任务已顺利完成全部操作！", "thought": "全部流程已顺利完成"})

                    if normalized_plan:
                        return normalized_plan
            except Exception as e:
                pass
        return []

    async def call_lm_studio_qwen(self, task_instruction: str, history_logs: List[str]) -> Dict[str, Any]:
        """调用本地 LM Studio Qwen 3B 进行快速指令解析与规划"""
        # 如果尚未生成任务规划，先生成完整规划
        if self._cached_plan is None:
            self._cached_plan = await self.plan_task_with_qwen(task_instruction)
            self._plan_step_idx = 0

        # 如果有缓存规划且尚未执行完，按部就班取出下一步执行
        if self._cached_plan and self._plan_step_idx < len(self._cached_plan):
            step_action = self._cached_plan[self._plan_step_idx]
            self._plan_step_idx += 1
            return step_action

        # 默认回退单步规划
        user_prompt = f"""当前用户任务: {task_instruction}
已经完成的历史步骤:
{chr(10).join(history_logs) if history_logs else '(无，这是第 1 步)'}

请判断当前应该执行哪一步，输出下一步的单行 JSON："""

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        state.add_log(f"🧠 [LM Studio Qwen 3B] 思考规划中...")
        
        # 自动尝试使用已加载的默认模型
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
        """调用 Gemini Flash (多模态) 进行屏幕视觉分析与操作"""
        if not self.gemini_client:
            raise RuntimeError("Gemini API Key 未配置，请在设置中配置或切换为 LM Studio 模式！")

        state.add_log("📷 正在截屏并调用 Gemini Flash 视觉模型...")
        # 截图压缩
        img_bytes = controller.capture_screen_bytes(max_width=1024, quality=70)
        
        prompt = f"""
{AGENT_SYSTEM_PROMPT}

当前用户目标：{task_instruction}
当前电脑屏幕截图已上传。
前面执行记录：
{chr(10).join(history_logs[-4:])}

根据屏幕当前画面，精准识别你需要点击或操作的元素坐标(0~1000归一化坐标)，并输出下一步操作的严格 JSON：
"""
        # 异步线程执行 Gemini 同步接口
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
        """通用 OpenAI 兼容端点调用器 (支持 OpenRouter, Groq, SiliconFlow, Cerebras 等)"""
        cfg = state.custom_models.get(model_id, {})
        base_url = cfg.get("base_url", "https://api.openai.com/v1")
        api_key = cfg.get("api_key") or "dummy-key"
        actual_model = cfg.get("model", "gpt-3.5-turbo")

        state.add_log(f"🌐 正在调用自定义端点 [{cfg.get('name', model_id)}]: {actual_model}...")

        custom_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=35.0,
        )

        prompt = f"""{AGENT_SYSTEM_PROMPT}

当前用户目标：{task_instruction}
前面操作记录：
{chr(10).join(history_logs[-4:])}

请给出下一步操作的严格单行 JSON："""

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
        AI 自动化循环入口，支持随时终止与超时控制。
        """
        # 重置中断标志与计划缓存
        state.stop_requested = False
        state.is_agent_busy = True
        self._cached_plan = None
        self._plan_step_idx = 0
        state.add_log(f"🚀 启动新任务: '{instruction}' (引擎: {state.active_model})")
        cat_hud.update_status("working", f"准备执行: {instruction[:20]}")

        step_count = 0
        history_actions = []

        try:
            while step_count < settings.MAX_AGENT_STEPS:
                step_count += 1

                # 每一轮循环首要检查是否被请求停止
                if state.stop_requested:
                    raise asyncio.CancelledError("任务已被用户紧急终止！")

                state.add_log(f"⏳ 步骤 [{step_count}/{settings.MAX_AGENT_STEPS}] 决策中...")

                # 1. 路由器决策：根据当前模型获取下一步动作
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
                    state.add_log(f"❌ 模型请求失败: {e}")
                    # 如果本地 LM Studio 连不上，给用户友好提醒
                    if "1234" in str(e):
                        return f"连接 LM Studio 失败，请检查 LM Studio 的 Local Server 是否开启在 http://localhost:1234: {e}"
                    return f"模型决策发生异常: {e}"

                # 再次检查停止
                if state.stop_requested:
                    raise asyncio.CancelledError("任务已被用户紧急终止！")

                action_type = action_plan.get("action", "").lower()
                thought = action_plan.get("thought", "")

                # 2. 检查是否需要向用户弹出提问 (ask_user)
                if action_type == "ask_user":
                    question_text = str(action_plan.get("param") or thought or "请确认接下来的操作:")
                    state.add_log(f"❓ LLM 向用户提问: {question_text}")
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
            # 无论成功完成还是中途停止，彻底释放 LM Studio 显存与模型！
            try:
                await self.unload_all_models()
            except Exception as ex:
                state.add_log(f"⚠️ 自动释放模型提示: {ex}")


agent_runner = LLMAgentRunner()
