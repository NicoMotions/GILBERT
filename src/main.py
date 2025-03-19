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
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

def get_ai_response(prompt):
    """Get response from OpenAI API."""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant for a creative agency. You help with client communication, project management, and creative tasks."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        return "I apologize, but I'm having trouble processing that request right now."

# Slack event handlers
@app.event("message")
def handle_message_events(body, logger):
    """Handle incoming messages."""
    event = body["event"]
    text = event.get("text", "").lower()
    user = event.get("user")
    channel = event.get("channel")
    
    # Ignore bot messages
    if "bot_id" in event:
        return
    
    # Handle remember command
    if "remember" in text:
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
    
    # Handle recall command
    elif "recall" in text:
        try:
            topic = text.split("recall", 1)[1].strip()
            data = read_from_sheet("Memory", "A:C")
            if data:
                # Filter and format relevant memories
                memories = [row for row in data if topic in row[2].lower()]
                if memories:
                    response = "Here's what I remember about that:\n"
                    for memory in memories[-5:]:  # Show last 5 relevant memories
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
    
    # Handle onboard command
    elif "onboard" in text:
        try:
            client_name = text.split("onboard", 1)[1].strip()
            # Create onboarding record
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[timestamp, client_name, "Started", user]]
            if append_to_sheet("Onboarding", values):
                app.client.chat_postMessage(
                    channel=channel,
                    text=f"Starting onboarding process for {client_name}! 🎉\n"
                         f"I'll guide you through the necessary steps."
                )
                # Send onboarding checklist
                checklist = [
                    "1. Send welcome email",
                    "2. Share project brief template",
                    "3. Schedule kickoff meeting",
                    "4. Set up project folder",
                    "5. Create client portal access"
                ]
                app.client.chat_postMessage(
                    channel=channel,
                    text="Here's the onboarding checklist:\n" + "\n".join(checklist)
                )
            else:
                app.client.chat_postMessage(
                    channel=channel,
                    text="Sorry, I couldn't start the onboarding process. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error in onboard command: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while trying to start the onboarding process."
            )
    
    # Handle AI chat command
    elif "ask" in text:
        try:
            prompt = text.split("ask", 1)[1].strip()
            ai_response = get_ai_response(prompt)
            app.client.chat_postMessage(
                channel=channel,
                text=ai_response
            )
        except Exception as e:
            logger.error(f"Error in AI chat: {e}")
            app.client.chat_postMessage(
                channel=channel,
                text="Sorry, I encountered an error while processing your request."
            )

# Initialize the handler for Railway
handler = SlackRequestHandler(app=app)

# For local development
if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start() 