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
        self.list_dir = os.path.join(os.path.dirname(__file__), "list")
        self.image_cache = {}
        
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
    
    def _find_character_by_style_id(self, pack_name, style_id):
        """根据样式ID查找对应的角色和样式"""
        characters = self._get_characters_in_pack(pack_name)
        
        for char_id, char_data in characters.items():
            char_styles = char_data["styles"]
            char_ids = char_data["id"]
            
            # 创建id到style的映射
            id_to_style = {id_val: style for id_val, style in zip(char_ids, char_styles)}
            
            if style_id in id_to_style:
                character_name = char_data["name"]
                style = id_to_style[style_id]
                return (character_name, style)
        
        return None
    
    def _load_image_as_base64(self, image_name):
        """加载图片并转换为base64"""
        if image_name in self.image_cache:
            return self.image_cache[image_name]
        
        try:
            image_path = os.path.join(self.list_dir, image_name)
            if not os.path.exists(image_path):
                logger.warning(f"图片不存在: {image_path}")
                return None
            
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                self.image_cache[image_name] = image_base64
                return image_base64
        except Exception as e:
            logger.error(f"加载图片失败 {image_name}: {e}")
            return None
    
    @filter.command("sticker")
    async def start_sticker_session(self, event: AstrMessageEvent):
        """开始贴纸生成会话或处理带参数的命令"""
        session_key = self._get_session_key(event)

        # 获取命令参数
        args = event.message_str.strip().split()
        # 移除命令本身，只保留参数
        if len(args) > 0 and args[0].startswith('/sticker'):
            args = args[1:]

        # 处理 /sticker list 命令
        if len(args) > 0 and args[0].lower() == "list":
            character_list_image = self._load_image_as_base64("characterListAll.jpeg")
            if character_list_image:
                yield event.chain_result([
                    Comp.Plain(text="所有角色列表："),
                    Comp.Image(file=f"base64://{character_list_image}")
                ])
            else:
                yield event.plain_result("无法加载角色列表图片")
            return

        # 处理 /sticker help 命令
        if len(args) > 0 and args[0].lower() == "help":
            help_text = """📖 贴纸生成器命令帮助

    命令列表：
    1. /sticker - 进入交互式模式选择贴纸包、角色、样式并输入文字
    2. /sticker list - 查看所有角色列表
    3. /sticker help - 查看此帮助信息
    4. /sticker <pack> <样式id> <文字> - 直接生成贴纸

    快速生成模式说明：
    - <pack>: 贴纸包名称（如：pjsk）
    - <样式id>: 0 到 358 之间的数字
    - <文字>: 要显示在贴纸上的文字

    交互式模式退出：
    - 在任何步骤输入 quit 可直接退出贴纸生成器

    例如：/sticker pjsk 42 你好"""
            yield event.plain_result(help_text)
            return

        # 处理 /sticker <pack> <样式id> <文字> 直接生成模式
        if len(args) >= 3:
            pack_name = args[0].lower()
            all_packs = self._get_all_packs()

            # 检查pack是否存在
            pack_found = None
            for pack in all_packs:
                if pack.lower() == pack_name:
                    pack_found = pack
                    break

            if pack_found:
                try:
                    style_id = int(args[1])
                    text = " ".join(args[2:])

                    # 查找对应的角色和样式
                    character_info = self._find_character_by_style_id(pack_found, style_id)
                    if character_info:
                        character_name, style = character_info

                        # 构建URL并生成贴纸
                        url = self._build_sticker_url(pack_found, character_name, style, text)

                        try:
                            async with httpx.AsyncClient() as client:
                                response = await client.get(url, timeout=30.0)
                                if response.status_code == 200:
                                    image_bytes = response.content
                                    image_base64 = base64.b64encode(image_bytes).decode('utf-8')

                                    yield event.chain_result([
                                        Comp.Image(file=f"base64://{image_base64}"),
                                        Comp.Plain(text=f"✨ 贴纸生成完成！\n角色：{character_name} | 样式ID：{style_id}\n文字：{text}")
                                    ])
                                else:
                                    yield event.plain_result(f"❌ 图片生成失败，状态码: {response.status_code}")
                        except Exception as e:
                            logger.error(f"下载图片时出错: {e}")
                            yield event.plain_result(f"❌ 图片下载失败: {str(e)}")
                    else:
                        yield event.plain_result(f"❌ 样式ID {style_id} 不存在，请输入 0 到 358 之间的数字")
                except ValueError:
                    yield event.plain_result(f"❌ 样式ID 必须是数字，请输入 0 到 358 之间的数字")
            else:
                yield event.plain_result(f"❌ 贴纸包 '{pack_name}' 不存在")
            return

        # 如果用户已有会话，先清除
        if session_key in self.sessions:
            del self.sessions[session_key]

        # 初始化新会话（交互式模式）
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

        yield event.plain_result(f"欢迎使用贴纸生成器！\n{pack_list_msg}\n\n💡 提示：任何时刻输入 quit 可直接退出")
    
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
        
        # 检查是否输入了quit命令
        if message.lower() == "quit":
            if session_key in self.sessions:
                del self.sessions[session_key]
            yield event.plain_result("已退出贴纸生成器，如需再次生成请输入 /sticker")
            return
        
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
        #character_list_msg = "请选择角色(输入数字):\n" + "\n".join([f"{char_id}. {char_data['name']}" for char_id, char_data in characters.items()])
        character_list_msg = "请选择角色(输入数字):"
        response_text = f"已选择贴纸包: {matched_pack}\n{character_list_msg}"
        
        character_list_image = self._load_image_as_base64("characterListWithIndex.jpeg")
        if character_list_image:
            return event.chain_result([
                Comp.Plain(text=response_text),
                Comp.Image(file=f"base64://{character_list_image}")
            ])
        
        return event.plain_result(response_text)
    
    async def _handle_character_selection(self, event: AstrMessageEvent, session: dict, message: str):
        """处理角色选择"""
        pack = session["pack"]
        characters = self._get_characters_in_pack(pack)
        
        # 检查输入是否是有效的角色ID
        if message not in characters:
            return event.plain_result("角色不存在，请重新输入角色数字:")
        
        character_data = characters[message]
        character_name = character_data["name"]
        session["character"] = character_name
        session["character_id"] = message
        session["step"] = "select_style"
        
        # 获取该角色的动作列表
        styles = character_data["styles"]
        id_list = character_data["id"]
        
        # 创建id到style的映射
        id_to_style = {id_val: style for id_val, style in zip(id_list, styles)}
        
        # 保存映射到会话中
        session["id_to_style"] = id_to_style

        # style_list_msg = "请选择动作(输入数字):\n" + "\n".join([f"{id_val}. 动作{style}" for id_val, style in id_to_style.items()])
        style_list_msg = "请选择动作(输入数字):"
        response_text = f"已选择角色: {character_name}\n{style_list_msg}"
        
        character_image = self._load_image_as_base64(f"{character_name}.jpeg")
        if character_image:
            return event.chain_result([
                Comp.Plain(text=response_text),
                Comp.Image(file=f"base64://{character_image}")
            ])
        
        return event.plain_result(response_text)
    
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
