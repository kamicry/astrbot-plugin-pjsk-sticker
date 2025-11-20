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
    
    def _get_all_packs(self):
        """获取所有可用的pack列表"""
        return list(self.list_data.get("packs", {}).keys())
    
    def _get_characters_in_pack(self, pack_name):
        """获取指定pack中的所有角色"""
        pack_data = self.list_data.get("packs", {}).get(pack_name, {})
        characters = pack_data.get("characters", {})
        return characters
    
    @filter.command("sticker")
    async def start_sticker_session(self, event: AstrMessageEvent):
        """开始贴纸生成会话"""
        session_key = self._get_session_key(event)
        
        # 如果用户已有会话，先清除
        if session_key in self.sessions:
            del self.sessions[session_key]
        
        # 初始化新会话
        self.sessions[session_key] = {
            "step": "select_pack",
            "pack": None,
            "character": None,
            "character_id": None,
            "style_id": None,
            "text": None
        }
        
        # 获取所有可用的pack列表
        all_packs = self._get_all_packs()
        pack_list_msg = "请选择贴纸包(输入名称):\n" + "\n".join([f"- {pack}" for pack in all_packs])
        
        yield event.plain_result(f"欢迎使用贴纸生成器！\n{pack_list_msg}")
    
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
        handler = None
        if step == "select_pack":
            handler = self._handle_pack_selection
        elif step == "select_character":
            handler = self._handle_character_selection
        elif step == "select_style":
            handler = self._handle_style_selection
        elif step == "input_text":
            handler = self._handle_text_input
        
        if handler is None:
            return
        
        result = await handler(event, session, message)
        if result is not None:
            yield result
    
    async def _handle_pack_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理pack选择"""
        all_packs = self._get_all_packs()
        
        # 尝试匹配pack名（不分大小写）
        matched_pack = None
        for pack in all_packs:
            if message.lower() == pack.lower():
                matched_pack = pack
                break
        
        if matched_pack is None:
            return event.plain_result("贴纸包不存在，请重新输入:")
        
        session["pack"] = matched_pack
        session["step"] = "select_character"
        
        # 获取该pack中的角色列表
        characters = self._get_characters_in_pack(matched_pack)
        character_list_msg = "请选择角色(输入数字):\n" + "\n".join([f"{char_id}. {char_data['name']}" for char_id, char_data in characters.items()])
        
        return event.plain_result(f"已选择贴纸包: {matched_pack}\n{character_list_msg}")
    
    async def _handle_character_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理角色选择"""
        pack = session["pack"]
        characters = self._get_characters_in_pack(pack)
        
        # 检查输入是否是有效的角色ID
        if message not in characters:
            return event.plain_result("角色不存在，请重新输入角色数字:")
        
        character_data = characters[message]
        session["character"] = character_data["name"]
        session["character_id"] = message
        session["step"] = "select_style"
        
        # 获取该角色的动作列表
        styles = character_data["styles"]
        id_list = character_data["id"]
        
        # 创建id到style的映射
        id_to_style = {id_val: style for id_val, style in zip(id_list, styles)}
        
        # 保存映射到会话中
        session["id_to_style"] = id_to_style
        
        style_list_msg = "请选择动作(输入数字):\n" + "\n".join([f"{id_val}. 动作{style}" for id_val, style in id_to_style.items()])
        
        return event.plain_result(f"已选择角色: {character_data['name']}\n{style_list_msg}")
    
    async def _handle_style_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理动作/样式选择"""
        try:
            selected_id = int(message)
            id_to_style = session.get("id_to_style", {})
            
            if selected_id not in id_to_style:
                return event.plain_result(f"请输入有效的动作数字，可选项: {', '.join(map(str, id_to_style.keys()))}")
            
            selected_style = id_to_style[selected_id]
            session["style_id"] = selected_style
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
    
    def _build_sticker_url(self, pack, character, style_id, text):
        """构建贴纸URL"""
        base_url = "https://next-sticker.vercel.app/api/overlay-text"
        image_path = f"https://raw.githubusercontent.com/kamicry/meme-stickers-hub/main/{pack}/{character}/{character}_{style_id}.png"
        
        encoded_text = urllib.parse.quote(text)
        
        return f"{base_url}?path={image_path}&key={encoded_text}"
    
    async def terminate(self):
        """插件销毁时清理资源"""
        self.sessions.clear()
        logger.info("贴纸插件已清理")
