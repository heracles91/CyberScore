# config.py — Configuration CyberScore
# Copiez ce fichier en config.py et renseignez vos valeurs
#   cp config.example.py config.py

# --- SMTP ---
# Compatible Office 365, Exchange, Gmail, etc.
SMTP_SERVER = "smtp.office365.com"
SMTP_PORT   = 25
SMTP_USER   = "ne-pas-repondre@votre-domaine.fr"
SMTP_PASS   = "votre-mot-de-passe-smtp"
SMTP_FROM_NAME = "CyberScore DSI"

# --- Administration ---
ADMIN_USER = "admin"
ADMIN_PASS = "changez-ce-mot-de-passe!"    # Obligatoire
SECRET_KEY = "generez-une-cle-longue-et-aleatoire-ici"  # ex: python -c "import secrets; print(secrets.token_hex(32))"

# --- Application ---
COMPANY_NAME         = "Votre Entreprise"
LEADERBOARD_URL      = "http://votre-serveur:5000/leaderboard"
DB_PATH              = "cyberscore.db"
SESSION_TIMEOUT_HOURS = 8

# --- Support ---
SUPPORT_EMAIL = "support@votre-domaine.fr"
