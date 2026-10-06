import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Model Configuration
MODEL_EXTRACTION = "gemini-3.5-flash-lite"
MODEL_RESEARCH = "gemini-3.8-flash"

# Credit Policy Thresholds
WEAKEST_LINK_THRESHOLD = 40.0
DSCR_REJECT_THRESHOLD = 1.20
BOUNCE_KNOCKOUT_THRESHOLD = 5
MAX_BOUNCES_TOLERANCE = 5
