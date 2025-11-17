from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import json
import os

@register("sticker_generator", "YourName", "贴纸生成插件", "1.0.0")
class StickerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.sessions = {}  # 存储用户会话状态
        self.list_data = {}  # 存储list.json数据
        
    async def initialize(self):
        """插件初始化，加载list.json数据"""
        try:
            # 假设list.json文件在插件目录下
            list_json_path = os.path.join(os.path.dirname(__file__), "list.json")
            with open(list_json_path, 'r', encoding='utf-8') as f:
                self.list_data = json.load(f)
            logger.info("贴纸数据加载成功")
        except Exception as e:
            logger.error(f"加载贴纸数据失败: {e}")
            
    @filter.command("sticker")
    async def start_sticker_session(self, event: AstrMessageEvent):
        """开始贴纸生成会话"""
        user_id = event.get_sender_id()
        
        # 如果用户已有会话，先清除
        if user_id in self.sessions:
            del self.sessions[user_id]
            
        # 初始化新会话
        self.sessions[user_id] = {
            "step": "select_pack",
            "pack": None,
            "character": None,
            "style_id": None,
            "text": None
        }
        
        # 获取pack列表
        packs = self._get_pack_list()
        pack_list_msg = "请选择贴纸包:\n" + "\n".join([f"- {pack}" for pack in packs])
        
        yield event.plain_result(f"欢迎使用贴纸生成器！\n{pack_list_msg}\n请输入贴纸包名称:")
        
    @filter.message()
    async def handle_sticker_session(self, event: AstrMessageEvent):
        """处理贴纸生成会话的各个步骤"""
        user_id = event.get_sender_id()
        message = event.message_str.strip()
        
        # 检查用户是否在会话中
        if user_id not in self.sessions:
            return
            
        session = self.sessions[user_id]
        current_step = session["step"]
        
        try:
            if current_step == "select_pack":
                await self._handle_pack_selection(event, user_id, message, session)
                
            elif current_step == "select_character":
                await self._handle_character_selection(event, user_id, message, session)
                
            elif current_step == "select_style":
                await self._handle_style_selection(event, user_id, message, session)
                
            elif current_step == "input_text":
                await self._handle_text_input(event, user_id, message, session)
                
        except Exception as e:
            logger.error(f"处理贴纸会话时出错: {e}")
            yield event.plain_result("处理过程中出现错误，请重新开始")
            del self.sessions[user_id]
    
    async def _handle_pack_selection(self, event, user_id, message, session):
        """处理贴纸包选择"""
        packs = self._get_pack_list()
        
        if message not in packs:
            yield event.plain_result("贴纸包不存在，请重新输入:")
            return
            
        session["pack"] = message
        session["step"] = "select_character"
        
        # 获取该pack下的角色列表
        characters = self._get_character_list(message)
        character_list_msg = "请选择角色:\n" + "\n".join([f"- {char}" for char in characters])
        
        yield event.plain_result(f"已选择贴纸包: {message}\n{character_list_msg}\n请输入角色名称:")
    
    async def _handle_character_selection(self, event, user_id, message, session):
        """处理角色选择"""
        pack = session["pack"]
        characters = self._get_character_list(pack)
        
        if message not in characters:
            yield event.plain_result("角色不存在，请重新输入:")
            return
            
        session["character"] = message
        session["step"] = "select_style"
        
        # 获取该角色的动作列表
        styles = self._get_style_list(pack, message)
        style_list_msg = "请选择动作(输入数字):\n" + "\n".join([f"{i+1}. 动作{i+1}" for i in range(len(styles))])
        
        yield event.plain_result(f"已选择角色: {message}\n{style_list_msg}")
    
    async def _handle_style_selection(self, event, user_id, message, session):
        """处理动作选择"""
        try:
            style_num = int(message)
            pack = session["pack"]
            character = session["character"]
            styles = self._get_style_list(pack, character)
            
            if style_num < 1 or style_num > len(styles):
                yield event.plain_result(f"请输入1-{len(styles)}之间的数字:")
                return
                
            # 个位数补零
            session["style_id"] = str(style_num).zfill(2)
            session["step"] = "input_text"
            
            yield event.plain_result("请输入要显示的文字:")
            
        except ValueError:
            yield event.plain_result("请输入有效的数字:")
    
    async def _handle_text_input(self, event, user_id, message, session):
        """处理文字输入并生成贴纸"""
        session["text"] = message
        
        # 构建URL
        url = self._build_sticker_url(
            session["pack"],
            session["character"], 
            session["style_id"],
            session["text"]
        )
        
        # 发送图片
        yield event.image_result(url)
        
        # 结束会话
        del self.sessions[user_id]
        yield event.plain_result("贴纸生成完成！如需再次生成，请输入 /sticker")
    
    def _get_pack_list(self):
        """获取贴纸包列表"""
        if "packs" in self.list_data:
            return list(self.list_data["packs"].keys())
        return []
    
    def _get_character_list(self, pack):
        """获取指定贴纸包的角色列表"""
        if (pack in self.list_data.get("packs", {}) and 
            "characters" in self.list_data["packs"][pack]):
            return list(self.list_data["packs"][pack]["characters"].keys())
        return []
    
    def _get_style_list(self, pack, character):
        """获取指定角色的动作列表"""
        try:
            character_data = self.list_data["packs"][pack]["characters"][character]
            if "styles" in character_data:
                return character_data["styles"]
            # 如果没有styles字段，默认提供10个动作
            return list(range(1, 11))
        except:
            # 如果获取失败，默认提供10个动作
            return list(range(1, 11))
    
    def _build_sticker_url(self, pack, character, style_id, text):
        """构建贴纸URL"""
        base_url = "https://next-sticker.vercel.app/api/overlay-text"
        image_path = f"https://raw.githubusercontent.com/kamicry/koishi-plugin-pjsk-pptr/main/src/assets/img/{pack}/{character}/{character}_{style_id}.png"
        
        # URL编码文字
        import urllib.parse
        encoded_text = urllib.parse.quote(text)
        
        return f"{base_url}?path={image_path}&key={encoded_text}"
    
    async def terminate(self):
        """插件销毁时清理资源"""
        self.sessions.clear()
        logger.info("贴纸插件已清理")