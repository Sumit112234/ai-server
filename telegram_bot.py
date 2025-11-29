import os
import json
import io
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -----------------------------------------
# Load Environment Variables
# -----------------------------------------
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not GEMINI_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

if not BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env")

# Gemini Client
client = genai.Client(api_key=GEMINI_KEY)

# -----------------------------------------
# Helper Paths (per user)
# -----------------------------------------
def user_memory_path(user_id): 
    return f"memory_{user_id}.json"

def user_history_path(user_id): 
    return f"history_{user_id}.json"

# -----------------------------------------
# Memory helpers
# -----------------------------------------
def load_memory(user_id):
    path = user_memory_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(user_id, memory):
    with open(user_memory_path(user_id), "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=4, ensure_ascii=False)

# -----------------------------------------
# History helpers
# -----------------------------------------
def save_history(user_id, sender, message):
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sender": sender,
        "message": message,
    }

    path = user_history_path(user_id)
    history = []

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []

    history.append(entry)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

# -----------------------------------------
# Commands
# -----------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Hello! I'm your Gemini-powered AI bot.\n"
        "You can:\n"
        "• Send text → I reply\n"
        "• Send an image (with or without caption) → I understand it\n\n"
        "Commands:\n"
        "/remember key=value – Save memory\n"
        "/memory – Show memory\n"
        "/clear – Clear chat history\n"
        "/stop – End chat"
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Chat stopped. Goodbye! 👋")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    path = user_history_path(user_id)
    # reset history
    with open(path, "w", encoding="utf-8") as f:
        f.write("[]")
    await update.message.reply_text("🧹 Chat history cleared!")

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    memory = load_memory(user_id)
    await update.message.reply_text(f"🧠 Memory:\n{json.dumps(memory, indent=4, ensure_ascii=False)}")

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    cmd = update.message.text.replace("/remember ", "", 1)

    if "=" not in cmd:
        await update.message.reply_text("Use format: /remember key=value")
        return

    key, value = cmd.split("=", 1)
    key, value = key.strip(), value.strip()

    memory = load_memory(user_id)
    memory[key] = value
    save_memory(user_id, memory)

    await update.message.reply_text(f"✔ Remembered: {key} = {value}")

# -----------------------------------------
# Text Chat Handler
# -----------------------------------------
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_msg = update.message.text

    # exit command (for text)
    if user_msg.lower() == "/stop":
        await stop(update, context)
        return

    # Load memory
    memory = load_memory(user_id)
    memory_text = "\n".join([f"{k}: {v}" for k, v in memory.items()])

    enhanced_input = f"{user_msg}\n\n(Memory:\n{memory_text})"

    # Create a chat session for this message
    chat_session = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(response_modalities=["TEXT"])
    )

    reply = chat_session.send_message(enhanced_input)

    ai_text = ""
    for part in reply.parts:
        if part.text:
            ai_text += part.text

    # Reply to user
    await update.message.reply_text(ai_text)

    # Save to history
    save_history(user_id, "You", user_msg)
    save_history(user_id, "AI", ai_text)

# -----------------------------------------
# Image Handler (Image Understanding)
# -----------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    caption = update.message.caption or ""

    # Load memory
    memory = load_memory(user_id)
    memory_text = "\n".join([f"{k}: {v}" for k, v in memory.items()])

    # Build prompt
    if caption.strip():
        prompt = f"{caption}\n\n(Memory:\n{memory_text})"
    else:
        prompt = f"Describe this image in detail.\n\n(Memory:\n{memory_text})"

    # Get the highest resolution photo
    photo = update.message.photo[-1]
    file = await photo.get_file()

    # Download to memory
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    image_bytes = bio.getvalue()

    # Create image part
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/jpeg",  # Telegram sends JPEG by default
    )

    # Call Gemini with image + text
    response = client.models.generate_content(
        model="gemini-2.5-flash",  # Vision-capable model
        contents=[
            image_part,
            prompt
        ]
    )

    ai_text = response.text or ""

    if not ai_text:
        # fallback: assemble manually
        try:
            for cand in response.candidates:
                for part in cand.content.parts:
                    if part.text:
                        ai_text += part.text
        except Exception:
            ai_text = "I couldn't understand this image properly, sorry."

    # Reply to user
    await update.message.reply_text(ai_text)

    # Save to history
    save_history(user_id, "You (image)", caption or "[Image]")
    save_history(user_id, "AI", ai_text)

# -----------------------------------------
# RUN BOT
# -----------------------------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("remember", remember))

    # Text messages (that are not commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    # Photo messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Bot running with text + image understanding...")
    app.run_polling()


# import os
# from dotenv import load_dotenv
# from google import genai
# from google.genai import types

# # Load .env
# load_dotenv()

# api_key = os.environ.get("GEMINI_API_KEY")
# if not api_key:
#     raise ValueError("GEMINI_API_KEY not found in .env")

# client = genai.Client(api_key=api_key)

# # Create chat session
# chat = client.chats.create(
#     model="gemini-2.5-flash",   # Free text-to-text model
#     config=types.GenerateContentConfig(response_modalities=["TEXT"])
# )

# print("Chat started! Type your prompt.\nType 'xxx' to exit.\n")

# while True:
#     user_input = input("You: ")

#     # Exit condition
#     if user_input.lower().strip() == "xxx":
#         print("Chat ended.")
#         break

#     # Send message to model
#     response = chat.send_message(user_input)

#     # Print model response
#     for part in response.parts:
#         if part.text:
#             print("\nAI:", part.text, "\n")


# import os
# from dotenv import load_dotenv
# from google import genai

# # Load .env
# load_dotenv()

# api_key = os.environ.get("GEMINI_API_KEY")

# if not api_key:
#     raise ValueError("GEMINI_API_KEY not found in .env")

# client = genai.Client(api_key=api_key)

# prompt = (
#     "Create a vibrant infographic that explains photosynthesis as if it were a recipe "
#     "for a plant's favorite food, with ingredients like sunlight, water, and CO2. "
#     "Design it like a colorful kids' cookbook page."
# )

# response = client.models.generate_images(
#     model="imagen-3.0-generate-1",
#     prompt=prompt
# )

# # Save generated image
# image = response.images[0]
# image.save("photosynthesis.png")
# print("Image saved as photosynthesis.png")


# import os
# from google import genai
# from google.genai import types

# from dotenv import load_dotenv
# load_dotenv()  # Loads .env into environment variables

# api_key = os.environ.get("GEMINI_API_KEY")

# if not api_key:
#     raise ValueError("GEMINI_API_KEY not found in .env")

# client = genai.Client(api_key=api_key)

# chat = client.chats.create(
#     model="gemini-3-pro-image-preview",
#     config=types.GenerateContentConfig(
#         response_modalities=['TEXT', 'IMAGE'],
#         tools=[{"google_search": {}}]
#     )
# )

# message = (
#     "Create a vibrant infographic that explains photosynthesis as if it were a recipe "
#     "for a plant's favorite food. Show the 'ingredients' (sunlight, water, CO2) and "
#     "the 'finished dish' (sugar/energy). The style should be like a colorful kids' "
#     "cookbook page for 4th graders."
# )

# response = chat.send_message(message)

# # Save or print content
# for part in response.parts:
#     if part.text:
#         print(part.text)
#     else:
#         image = part.as_image()
#         if image:
#             image.save("photosynthesis.png")
#             print("Image saved as photosynthesis.png")
