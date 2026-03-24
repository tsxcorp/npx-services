import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

MAILGUN_API_KEY = os.getenv('MAILGUN_API_KEY', '')
MAILGUN_DOMAIN = os.getenv('MAILGUN_DOMAIN', '')
MAILGUN_API_URL = os.getenv('MAILGUN_API_URL', 'https://api.mailgun.net')

DIRECTUS_URL = os.getenv('DIRECTUS_URL', 'https://app.nexpo.vn')
DIRECTUS_ADMIN_TOKEN = os.getenv('DIRECTUS_ADMIN_TOKEN', '')

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')

# App base URLs — dùng để build absolute links trong notifications
# Exhibitor (portal) → PORTAL_URL, Organizer (admin) → ADMIN_URL
PORTAL_URL = os.getenv('PORTAL_URL', 'https://portal.nexpo.vn')
ADMIN_URL = os.getenv('ADMIN_URL', 'https://platform.nexpo.vn')

# Global semaphore: max 5 concurrent AI scoring calls across ALL matching requests
# Prevents OpenRouter rate-limit (429) when multiple exhibitors run simultaneously
ai_semaphore = asyncio.Semaphore(5)
