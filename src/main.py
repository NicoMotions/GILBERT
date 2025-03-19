import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import openai
from flask import Flask
import threading
import sys

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app for health check
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return 'OK', 200

def run_flask():
    try:
        port = int(os.environ.get('PORT', 8000))
        logger.info(f"Starting Flask server on port {port}")
        flask_app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Flask server error: {e}")
        sys.exit(1)

# Initialize OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Initialize Slack app with signing secret
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    try:
        # For Railway, we'll use the service account JSON directly from environment
        service_account_info = eval(os.environ.get("GOOGLE_SERVICE_ACCOUNT", "{}"))
        if service_account_info:
            creds = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=SCOPES
            )
        else:
            # Fallback to file-based credentials
            creds = service_account.Credentials.from_service_account_file(
                os.environ["GOOGLE_SHEETS_CREDENTIALS_PATH"],
                scopes=SCOPES
            )
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Error initializing Google Sheets service: {e}")
        return None

def append_to_sheet(sheet_name, values):
    """Append data to specified Google Sheet."""
    try:
        service = get_google_sheets_service()
        if not service:
            return False
        
        body = {
            'values': values
        }
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{sheet_name}!A:Z',
            valueInputOption='RAW',
            body=body
        ).execute()
        return True
    except HttpError as e:
        logger.error(f"Error appending to sheet: {e}")
        return False

def read_from_sheet(sheet_name, range_name):
    """Read data from specified Google Sheet."""
    try:
        service = get_google_sheets_service()
        if not service:
            return None
        
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{sheet_name}!{range_name}'
        ).execute()
        return result.get('values', [])
    except HttpError as e:
        logger.error(f"Error reading from sheet: {e}")
        return None

def get_ai_response(prompt, context=None):
    """Get response from OpenAI API with context."""
    try:
        messages = [
            {"role": "system", "content": "You are Gilbert AI, a helpful and friendly assistant for a creative agency. You help with client communication, project management, and creative tasks. You have a conversational tone and remember important information from conversations. If you don't know something, say so and offer to help find the answer."}
        ]
        
        if context:
            messages.append({"role": "system", "content": f"Context from previous conversations: {context}"})
        
        messages.append({"role": "user", "content": prompt})
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return "I apologize, but I'm having trouble processing that request right now."

def extract_important_info(text):
    """Extract important information from text using AI."""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extract important information from the text that should be remembered. Focus on facts, decisions, deadlines, and key details. Return only the important information in a concise format."},
                {"role": "user", "content": text}
            ],
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error extracting important info: {e}")
        return None

# Slack event handlers
@app.event("message")
def handle_message_events(body, logger):
    """Handle incoming messages."""
    event = body["event"]
    text = event.get("text", "")
    user = event.get("user")
    channel = event.get("channel")
    
    # Log the received message
    logger.info(f"Received message: {text}")
    
    # Ignore bot messages
    if "bot_id" in event:
        logger.info("Ignoring bot message")
        return
    
    # Log mention checks
    logger.info(f"Checking for mentions in: {text}")
    logger.info(f"Contains <@Gilbert AI>: {'<@Gilbert AI>' in text}")
    logger.info(f"Contains <@gilbert ai>: {'<@gilbert ai>' in text}")
    logger.info(f"Contains <@GilbertAI>: {'<@GilbertAI>' in text}")
    
    # Check if the message is directed at Gilbert AI
    if "<@Gilbert AI>" in text or "<@gilbert ai>" in text or "<@GilbertAI>" in text:
        logger.info("Gilbert AI mention detected!")
        # Remove the bot mention from the text
        clean_text = text.replace("<@Gilbert AI>", "").replace("<@gilbert ai>", "").replace("<@GilbertAI>", "").strip()
        logger.info(f"Cleaned text: {clean_text}")
        
        # Get context from previous conversations
        context_data = read_from_sheet("Memory", "A:C")
        context = ""
        if context_data:
            # Get last 5 relevant memories
            relevant_memories = [row[2] for row in context_data[-5:]]
            context = " ".join(relevant_memories)
            logger.info(f"Context from previous conversations: {context}")
        
        # Get AI response
        response = get_ai_response(clean_text, context)
        logger.info(f"AI response: {response}")
        
        # Extract and store important information
        important_info = extract_important_info(clean_text)
        if important_info:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, user, important_info]]
            append_to_sheet("Memory", values)
            logger.info(f"Stored important info: {important_info}")
        
        # Send response
        app.client.chat_postMessage(
            channel=channel,
            text=response
        )
        logger.info("Response sent successfully")
    
    # Handle specific commands for backward compatibility
    elif "remember" in text:
        try:
            info = text.split("remember", 1)[1].strip()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, user, info]]
            if append_to_sheet("Memory", values):
                app.client.chat_postMessage(
                    channel=channel,
                    text="I've remembered that information! 📝"
                )
            else:
                app.client.chat_postMessage(
                    channel=channel,
                    text="Sorry, I couldn't save that information. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error in remember command: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while trying to remember that."
            )
    
    elif "recall" in text:
        try:
            topic = text.split("recall", 1)[1].strip()
            data = read_from_sheet("Memory", "A:C")
            if data:
                memories = [row for row in data if topic in row[2].lower()]
                if memories:
                    response = "Here's what I remember about that:\n"
                    for memory in memories[-5:]:
                        response += f"• {memory[2]} (from {memory[1]} on {memory[0]})\n"
                    app.client.chat_postMessage(
                        channel=channel,
                        text=response
                    )
                else:
                    app.client.chat_postMessage(
                        channel=channel,
                        text="I don't have any memories about that topic yet."
                    )
            else:
                app.client.chat_postMessage(
                    channel=channel,
                    text="I don't have any memories stored yet."
                )
        except Exception as e:
            logger.error(f"Error in recall command: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while trying to recall that information."
            )

# Initialize the handler for Socket Mode
if __name__ == "__main__":
    try:
        # Start the Flask server in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Start the Slack bot
        logger.info("Starting Slack bot...")
        handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        handler.start()
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1) 