import html
import json
import logging
import mimetypes
import os
import traceback
import uuid
from os import path

import requests
from telegram import File, ParseMode, Update
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    Defaults,
    Filters,
    MessageHandler,
    Updater,
)

from .s3bucket import (
    copy_file as s3_copy_file,
)
from .s3bucket import (
    delete_file as s3_delete_file,
)
from .s3bucket import (
    file_exist as s3_file_exist,
)
from .s3bucket import (
    get_file_acl as s3_get_file_acl,
)
from .s3bucket import (
    get_meta as s3_get_meta,
)
from .s3bucket import (
    get_obj_url as s3_get_obj_url,
)
from .s3bucket import (
    list_files as s3_list_files,
)
from .s3bucket import (
    make_private as s3_make_private,
)
from .s3bucket import (
    make_public as s3_make_public,
)
from .s3bucket import (
    upload_file as s3_upload_file,
)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# 从 @botfather 获取的机器人token
TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
TELEGRAM_USERNAME = os.getenv('TELEGRAM_USERNAME')

# 开发者聊天ID - 可以是个人ID或开发群组ID
# 使用机器人的 /start 命令可以查看你的聊天ID
DEVELOPER_CHAT_ID = os.getenv('DEVELOPER_CHAT_ID')

TEMP_PATH = os.getenv('TEMP_PATH', '/tmp')

DIGITALOCEAN_TOKEN = os.getenv('DIGITALOCEAN_TOKEN')
BUCKET_NAME = None
if os.getenv('BUCKET_NAME', '').strip():
    BUCKET_NAME = os.getenv('BUCKET_NAME')
ENDPOINT_URL = None
if os.getenv('ENDPOINT_URL', '').strip():
    ENDPOINT_URL = os.getenv('ENDPOINT_URL')
CUSTOM_ENDPOINT_URL = None
if os.getenv('CUSTOM_ENDPOINT_URL', '').strip():
    CUSTOM_ENDPOINT_URL = os.getenv('CUSTOM_ENDPOINT_URL')

CURRENT_UPLOAD_PATH = None


def start(update: Update, context: CallbackContext) -> None:
    """处理 /start 命令"""
    if update.effective_message.from_user.username != TELEGRAM_USERNAME:
        update.effective_message.reply_html(
            f'<b>无权访问</b>\n\n'
            f'你的聊天ID: <code>{update.effective_chat.id}</code>\n'
            f'你的用户名: <code>{update.effective_message.from_user.username}</code>'
        )
    else:
        update.message.reply_text("你好,我是你的文件管理助手")


def help_command(update: Update, context: CallbackContext) -> None:
    """处理 /help 命令"""
    update.message.reply_text("需要帮助吗？我是你的文件管理助手")


def echo(update: Update, context: CallbackContext) -> None:
    """复读用户消息"""
    update.message.reply_text(update.message.text)


def bad_command(update: Update, context: CallbackContext) -> None:
    """触发错误处理器"""
    raise Exception("出现错误，请稍后重试")


def upload_file(update: Update, context: CallbackContext) -> None:
    attachment = update.message.effective_attachment
    if isinstance(attachment, list):
        attachment = attachment[-1]

    # @see https://core.telegram.org/bots/api#getfile
    if attachment.file_size > 20 * 1024 * 1024:
        update.message.reply_html(
            '<b>文件太大</b>\n\n'
            '目前机器人<a href="https://core.telegram.org/bots/api#getfile">仅支持上传20MB以内的文件</a>\n'
        )
        return

    file = attachment.get_file()

    def get_original_file_name():
        original_file_name = path.basename(file.file_path)
        if hasattr(attachment, 'file_name'):
            original_file_name = attachment.file_name
        return original_file_name

    file_name = get_original_file_name()
    if update.message.caption is not None:
        if update.message.caption.strip():
            file_name = update.message.caption.strip().lstrip('/')
            if file_name.endswith('/'):
                file_name += get_original_file_name()
    else:
        if CURRENT_UPLOAD_PATH:
            if CURRENT_UPLOAD_PATH.endswith('/'):
                file_name = CURRENT_UPLOAD_PATH + get_original_file_name()
            else:
                file_name = CURRENT_UPLOAD_PATH + '/' + get_original_file_name()

    mime_type = mimetypes.MimeTypes().guess_type(file_name)[0]
    if hasattr(attachment, 'mime_type'):
        mime_type = attachment.mime_type

    tmp_file_name = f'{TEMP_PATH}/{uuid.uuid4()}'
    file = File.download(file, tmp_file_name)
    s3_upload_file(file, file_name, mime_type, 'public-read')  # 默认公开访问
    try:
        os.unlink(tmp_file_name)
    except Exception as e:
        logger.error(e)
    s3_file_path = s3_get_obj_url(file_name)
    update.message.reply_text(text=s3_file_path)


def delete_file(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0]
    if CUSTOM_ENDPOINT_URL in file_name:
        file_name = file_name.replace(CUSTOM_ENDPOINT_URL, '')
    file_name = file_name.strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_delete_file(file_name)
        update.message.reply_text(
            text=f'文件 {s3_file_path} 已删除，请记得清理CDN缓存'
        )
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def make_public(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_make_public(file_name)
        update.message.reply_text(text=f'文件 {s3_file_path} 已设为公开访问')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def make_private(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_make_private(file_name)
        update.message.reply_text(text=f'文件 {s3_file_path} 已设为私有访问')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def file_exist(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        if s3_file_exist(file_name):
            update.message.reply_text(text=f'文件 {s3_file_path} 存在')
            return
        update.message.reply_text(text=f'文件 {s3_file_path} 不存在')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def copy_file(update: Update, context: CallbackContext):
    if len(context.args) < 2:
        return

    src = context.args[0].strip().lstrip('/')
    dest = context.args[1].strip().lstrip('/')
    try:
        s3_src_path = s3_get_obj_url(src)
        if not s3_file_exist(src):
            update.message.reply_text(text=f'源文件 {s3_src_path} 不存在')
            return

        s3_dest_path = s3_get_obj_url(dest)
        s3_copy_file(src, dest)
        update.message.reply_text(
            text=f'文件已从 {s3_src_path} 复制到 {s3_dest_path}'
        )
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def get_file_acl(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        acl = s3_get_file_acl(file_name)
        update.message.reply_text(text=f'文件 {s3_file_path} 的访问权限为 {acl}')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def list_files(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        prefix = ''
    else:
        prefix = context.args[0].strip().lstrip('/')

    limit = 10
    if len(context.args) == 2:
        limit = int(context.args[1])
    entries = s3_list_files(prefix, limit=limit)
    if len(entries) == 0:
        update.message.reply_text(text='未找到文件')
        return

    message = '\n'.join(list(map(lambda entry: s3_get_obj_url(entry['key']), entries)))
    update.message.reply_text(text=message)


def get_metadata(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        response = s3_get_meta(file_name)
        logger.info(response)
        update.message.reply_text(text=f'{response}')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def purge_cache(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    if DIGITALOCEAN_TOKEN is None:
        raise Exception('服务不可用')

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        endpoint_url = ENDPOINT_URL.lstrip('https://')
        origin = f'{BUCKET_NAME}.{endpoint_url}'
        headers = {
            'Authorization': f'Bearer {DIGITALOCEAN_TOKEN}',
            'Content-Type': 'application/json',
        }
        api_url = 'https://api.digitalocean.com/v2/cdn/endpoints'
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if 'endpoints' not in data:
            raise Exception('未找到CDN节点')

        endpoints = list(
            filter(lambda endpoint: endpoint['origin'] == origin, data['endpoints'])
        )
        if len(endpoints) == 0:
            raise Exception('未找到CDN节点')

        endpoint_id = endpoints[0]['id']
        logger.info(endpoint_id)

        api_url = f'https://api.digitalocean.com/v2/cdn/endpoints/{endpoint_id}/cache'
        response = requests.delete(
            api_url, headers=headers, json={'files': [file_name]}
        )
        response.raise_for_status()
        update.message.reply_text(
            text=f'文件 {s3_file_path} 的CDN缓存已清理'
        )
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'错误: {e}')


def set_path(update: Update, context: CallbackContext):
    if len(context.args) != 0:
        global CURRENT_UPLOAD_PATH
        CURRENT_UPLOAD_PATH = context.args[0].strip().lstrip('/')
        update.message.reply_text(text=f'当前上传路径已设置为 {CURRENT_UPLOAD_PATH}')
    else:
        global PATH
        CURRENT_UPLOAD_PATH = None
        update.message.reply_text(text='当前上传路径已清除')


def get_path(update: Update, context: CallbackContext):
    update.message.reply_text(text=f'当前上传路径: {CURRENT_UPLOAD_PATH}')


def error_handler(update: Update, context: CallbackContext) -> None:
    """错误处理器 - 记录日志并通知开发者"""
    logger.error(msg="处理更新时发生异常:", exc_info=context.error)

    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
    tb_string = ''.join(tb_list)

    message = (
        f'处理更新时发生异常\n'
        f'<pre>update = {html.escape(json.dumps(update.to_dict(), indent=2, ensure_ascii=False))}'
        '</pre>\n\n'
        f'<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n'
        f'<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n'
        f'<pre>{html.escape(tb_string)}</pre>'
    )

    chat_id = DEVELOPER_CHAT_ID
    if chat_id is None:
        chat_id = update.effective_chat.id

    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.HTML)


def main():
    """启动机器人"""
    defaults = Defaults(disable_web_page_preview=True)
    updater = Updater(TELEGRAM_API_TOKEN, use_context=True, defaults=defaults)

    dispatcher = updater.dispatcher

    # 注册命令处理器
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(
        CommandHandler(
            'bad_command', bad_command, Filters.user(username=TELEGRAM_USERNAME)
        )
    )

    # 处理普通文本消息
    dispatcher.add_handler(
        MessageHandler(
            Filters.text & Filters.user(username=TELEGRAM_USERNAME) & ~Filters.command,
            echo,
        )
    )

    # 处理文件上传
    dispatcher.add_handler(
        MessageHandler(
            (
                Filters.photo
                | Filters.attachment
                | Filters.audio
                | Filters.video
                | Filters.animation
                | Filters.document
            )
            & Filters.user(username=TELEGRAM_USERNAME)
            & ~Filters.command,
            upload_file,
        )
    )

    # 文件删除命令
    dispatcher.add_handler(
        CommandHandler(
            'delete',
            delete_file,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 设置文件为公开访问
    dispatcher.add_handler(
        CommandHandler(
            'make_public',
            make_public,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 设置文件为私有访问
    dispatcher.add_handler(
        CommandHandler(
            'make_private',
            make_private,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 检查文件是否存在
    dispatcher.add_handler(
        CommandHandler(
            'exist',
            file_exist,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 文件复制/移动/重命名
    dispatcher.add_handler(
        CommandHandler(
            'copy_file',
            copy_file,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 获取文件访问权限
    dispatcher.add_handler(
        CommandHandler(
            'get_file_acl',
            get_file_acl,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 列出存储桶中的文件
    dispatcher.add_handler(
        CommandHandler(
            'list', list_files, Filters.user(username=TELEGRAM_USERNAME), pass_args=True
        )
    )

    # 获取文件元数据
    dispatcher.add_handler(
        CommandHandler(
            'get_meta',
            get_metadata,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 清理CDN缓存
    dispatcher.add_handler(
        CommandHandler(
            'purge_cache',
            purge_cache,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 设置上传路径
    dispatcher.add_handler(
        CommandHandler(
            'set_path',
            set_path,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 获取当前上传路径
    dispatcher.add_handler(
        CommandHandler(
            'get_path',
            get_path,
            Filters.user(username=TELEGRAM_USERNAME),
            pass_args=True,
        )
    )

    # 注册错误处理器
    dispatcher.add_error_handler(error_handler)

    # 启动机器人
    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
