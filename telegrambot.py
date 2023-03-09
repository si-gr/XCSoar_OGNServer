import telegram
from telegram.ext import Updater, CallbackContext, CallbackQueryHandler, MessageHandler, CommandHandler, filters, Application
#from telegram.utils import helpers
from telegram import Update, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
from pathlib import Path

class TelegramBot:

    __filename = "names.csv"
    __adminId = 0

    async def add(self, update: Update, context: CallbackContext):
        if(update.effective_user.id == int(self.__adminId) and context.args is not None and len(context.args) == 1 and update.message is not None):
            if(len(context.args[0]) > 0 and "," in context.args[0]):
                out = open(self.__filename, "a")
                out.write(context.args[0] + "\n")
                out.close()
                await update.message.reply_markdown_v2("added " + context.args[0].replace(".","\\."))
    
    async def delete(self, update: Update, context: CallbackContext):
        if(update.effective_user.id == int(self.__adminId) and context.args is not None and len(context.args) == 1 and update.message is not None):
            if(len(context.args[0]) > 0):
                names_file = open(self.__filename, "r")
                all_names = names_file.readlines()
                names_file.close()
                names_file = open(self.__filename, "w")
                deleted = False
                for n in all_names:
                    if not context.args[0] in n:
                        names_file.write(n)
                    else:
                        deleted = True
                names_file.close()
                if deleted:
                    await update.message.reply_markdown_v2("deleted " + context.args[0].replace(".","\\."))
                else:
                    await update.message.reply_markdown_v2("not found " + context.args[0].replace(".","\\."))

    def create_telegram_bot(self):
        priv_key_file = open("private.key", "r")
        priv_key = priv_key_file.readlines()
        priv_key_file.close()
        
        adminChatIdPath = Path("adminChat.id")
        self.__adminId = adminChatIdPath.read_bytes()
        print(self.__adminId)
        application = Application.builder().token(priv_key[0]).build()
        add_handler = CommandHandler('a', self.add)
        del_handler = CommandHandler('d', self.delete)
        application.add_handler(add_handler)
        application.add_handler(del_handler)
        #await application.initialize()
        #await application.updater.start_polling()
        application.run_polling()
        #await application.start()
