from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
import json
import os
import re
import urllib.parse
import base64
import httpx

@register("astrbot_plugin_pjsk_sticker", "kamicry", "pjsk表情包生成器", "v1.0.0")
class StickerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.sessions = {}
        self.list_data = {}
        
    async def initialize(self):
        """插件初始化，加载list.json数据"""
        try:
            list_json_path = os.path.join(os.path.dirname(__file__), "list.json")
            with open(list_json_path, 'r', encoding='utf-8') as f:
                self.list_data = json.load(f)
            logger.info("贴纸数据加载成功")
        except Exception as e:
            logger.error(f"加载贴纸数据失败: {e}")
            
    def _get_session_key(self, event: AstrMessageEvent):
        """获取会话key，使用(platform, sender_id)元组"""
        message_obj = getattr(event, "message_obj", None)
        platform = None
        if message_obj is not None:
            platform = getattr(message_obj, "platform", None)
            if platform is None:
                inner = getattr(message_obj, "message_obj", None)
                platform = getattr(inner, "platform", None) if inner is not None else None
        if platform is None:
            platform = getattr(event, "platform", None)
        platform_identifier = "default"
        if platform is not None:
            platform_identifier = str(getattr(platform, "name", platform))
        sender_id = event.get_sender_id()
        sender_identifier = "unknown" if sender_id is None else str(sender_id)
        return (platform_identifier, sender_identifier)
    
    def _get_all_characters(self):
        """获取所有可用的角色列表"""
        characters = []
        for pack_data in self.list_data.get("packs", {}).values():
            for character_name in pack_data.get("characters", {}).keys():
                if character_name not in characters:
                    characters.append(character_name)
        return characters
    
    def _get_pack_for_character(self, character):
        """根据角色名获取对应的pack"""
        for pack_name, pack_data in self.list_data.get("packs", {}).items():
            if character in pack_data.get("characters", {}):
                return pack_name
        return None
    
    @filter.command("sticker")
    async def start_sticker_session(self, event: AstrMessageEvent):
        """开始贴纸生成会话"""
        session_key = self._get_session_key(event)
        
        # 如果用户已有会话，先清除
        if session_key in self.sessions:
            del self.sessions[session_key]
        
        # 初始化新会话
        self.sessions[session_key] = {
            "step": "select_character",
            "pack": None,
            "character": None,
            "style_id": None,
            "text": None
        }
        
        # 获取所有可用的角色列表
        all_characters = self._get_all_characters()
        character_list_msg = "请选择角色:\n" + "\n".join([f"- {char}" for char in all_characters])
        
        yield event.plain_result(f"欢迎使用贴纸生成器！\n{character_list_msg}\n请输入角色名称:")
    
    @filter.regex(r'.*', flags=re.IGNORECASE)
    async def handle_session_message(self, event: AstrMessageEvent):
        """统一处理会话中的消息"""
        session_key = self._get_session_key(event)
        
        # 如果没有活跃会话，不处理
        if session_key not in self.sessions:
            return
        
        session = self.sessions[session_key]
        step = session["step"]
        message = event.message_str.strip()
        
        # 根据当前步骤路由到对应的处理逻辑
        if step == "select_character":
            yield self._handle_character_selection(event, session, message)
        elif step == "select_style":
            yield self._handle_style_selection(event, session, message)
        elif step == "input_text":
            yield self._handle_text_input(event, session, message)
    
    async def _handle_character_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理角色选择"""
        all_characters = self._get_all_characters()
        
        # 尝试匹配角色名（不分大小写）
        matched_character = None
        for char in all_characters:
            if message.lower() == char.lower():
                matched_character = char
                break
        
        if matched_character is None:
            return event.plain_result("角色不存在，请重新输入:")
        
        session["character"] = matched_character
        session["pack"] = self._get_pack_for_character(matched_character)
        session["step"] = "select_style"
        
        # 获取该角色的动作列表
        styles = self._get_style_list(session["pack"], matched_character)
        style_list_msg = "请选择动作(输入数字):\n" + "\n".join([f"{i+1}. 动作{i+1}" for i in range(len(styles))])
        
        return event.plain_result(f"已选择角色: {matched_character}\n{style_list_msg}")
    
    async def _handle_style_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理动作/样式选择"""
        try:
            style_num = int(message)
            pack = session["pack"]
            character = session["character"]
            styles = self._get_style_list(pack, character)
            
            if style_num < 1 or style_num > len(styles):
                return event.plain_result(f"请输入1-{len(styles)}之间的数字:")
            
            # 个位数补零
            session["style_id"] = str(style_num).zfill(2)
            session["step"] = "input_text"
            
            return event.plain_result("请输入要显示的文字:")
            
        except ValueError:
            return event.plain_result("请输入有效的数字:")
    
    async def _handle_text_input(self, event: AstrMessageEvent, session: dict, message: str):
        """处理文字输入并生成贴纸"""
        session_key = self._get_session_key(event)
        
        try:
            session["text"] = message
            
            # 构建URL
            url = self._build_sticker_url(
                session["pack"],
                session["character"], 
                session["style_id"],
                session["text"]
            )
            
            # 下载图片并转换为base64
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=30.0)
                    if response.status_code == 200:
                        image_bytes = response.content
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                        
                        # 结束会话
                        if session_key in self.sessions:
                            del self.sessions[session_key]
                        
                        # 使用 base64:// URI 格式发送图片
                        return event.chain_result([
                            Comp.Image(file=f"base64://{image_base64}"),
                            Comp.Plain(text="贴纸生成完成！如需再次生成，请输入 /sticker")
                        ])
                    else:
                        logger.error(f"下载图片失败，状态码: {response.status_code}")
                        if session_key in self.sessions:
                            del self.sessions[session_key]
                        return event.plain_result(f"图片生成失败，请重试。如需再次生成，请输入 /sticker")
            except Exception as e:
                logger.error(f"下载图片时出错: {e}")
                if session_key in self.sessions:
                    del self.sessions[session_key]
                return event.plain_result(f"图片下载失败: {str(e)}\n如需再次生成，请输入 /sticker")
            
        except Exception as e:
            logger.error(f"处理贴纸会话时出错: {e}")
            if session_key in self.sessions:
                del self.sessions[session_key]
            return event.plain_result("处理过程中出现错误，请重新开始")
    
    def _get_style_list(self, pack, character):
        """获取指定角色的动作列表"""
        try:
            character_data = self.list_data["packs"][pack]["characters"][character]
            if "styles" in character_data:
                return character_data["styles"]
            return list(range(1, 11))
        except:
            return list(range(1, 11))
    
    def _build_sticker_url(self, pack, character, style_id, text):
        """构建贴纸URL"""
        base_url = "https://next-sticker.vercel.app/api/overlay-text"
        image_path = f"https://raw.githubusercontent.com/kamicry/koishi-plugin-pjsk-pptr/main/src/assets/img/{pack}/{character}/{character}_{style_id}.png"
        
        encoded_text = urllib.parse.quote(text)
        
        return f"{base_url}?path={image_path}&key={encoded_text}"
    
    async def terminate(self):
        """插件销毁时清理资源"""
        self.sessions.clear()
        logger.info("贴纸插件已清理")
