# CyberScore — Système de gamification cybersécurité

Plateforme web interne de gamification pour sensibiliser les collaborateurs à la cybersécurité.
Chaque bon réflexe (signalement d'un phishing, réussite d'un quiz, vigilance…) rapporte des points.
Un leaderboard public affiche le classement en temps réel.

---

## Fonctionnalités

- **Score individuel** par collaborateur avec bonus et malus configurables
- **Quizzes** : création, envoi par email, correction détaillée, relance des non-répondants
- **Leaderboard public** (écran de salle de pause, aucune authentification requise)
- **Page Règles** publique listant le barème complet (`/regles`)
- **Notifications email** automatiques à chaque événement (SMTP compatible Office 365)
- **Import CSV** en masse des utilisateurs
- **Intégration GoPhish** via webhook JSON
- **Niveaux** : Débutant → Vigilant → Gardien → Expert

---

## Installation — Debian 12 (recommandé)

### 1. Pré-requis système

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

### 2. Créer un utilisateur dédié et cloner le projet

```bash
sudo useradd -r -m -d /opt/cyberscore -s /bin/bash cyberscore
sudo -u cyberscore git clone https://github.com/heracles91/cyberscore /opt/cyberscore
```

### 3. Environnement virtuel et dépendances

```bash
cd /opt/cyberscore
sudo -u cyberscore python3 -m venv venv
sudo -u cyberscore venv/bin/pip install -r requirements.txt
```

### 4. Configuration

```bash
sudo -u cyberscore cp config.example.py config.py
sudo -u cyberscore nano config.py   # Renseignez SMTP, admin, SECRET_KEY, etc.
```

> **Important** : `config.py` n'est pas versionné (voir `.gitignore`). Ne le commitez jamais.
>
> Pour générer une `SECRET_KEY` solide :
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

### 5. Lancement automatique au démarrage (systemd)

```bash
# Copier le fichier service
sudo cp /opt/cyberscore/cyberscore.service /etc/systemd/system/

# Activer et démarrer
sudo systemctl daemon-reload
sudo systemctl enable cyberscore
sudo systemctl start cyberscore

# Vérifier le statut
sudo systemctl status cyberscore
```

L'application tourne sur `http://0.0.0.0:5000` et redémarre automatiquement après un crash ou un reboot.

**Consulter les logs :**
```bash
sudo journalctl -u cyberscore -f
```

**Redémarrer après une mise à jour :**
```bash
cd /opt/cyberscore
sudo -u cyberscore git pull
sudo systemctl restart cyberscore
```

---

## Accès

| URL | Description |
|-----|-------------|
| `http://VOTRE-IP:5000/login` | Interface d'administration |
| `http://VOTRE-IP:5000/leaderboard` | Leaderboard public (écran TV) |
| `http://VOTRE-IP:5000/regles` | Règles du jeu & barème de points |

---

## Installation rapide (développement)

```bash
git clone https://github.com/heracles91/cyberscore
cd cyberscore
pip install -r requirements.txt
cp config.example.py config.py
# Éditez config.py
python3 app.py
```

---

## Structure des fichiers

```
cyberscore/
├── app.py                  # Routes Flask
├── config.example.py       # Template de configuration (config.py à créer, non versionné)
├── database.py             # Accès SQLite — init + CRUD
├── models.py               # Logique métier
├── mailer.py               # Envoi d'emails HTML (smtplib)
├── import_users.py         # Import CSV en masse
├── cyberscore.service      # Service systemd (Debian/Ubuntu)
├── requirements.txt
├── templates/
│   ├── base.html                     # Layout commun (navbar, flash)
│   ├── login.html
│   ├── dashboard.html
│   ├── users.html / user_add.html / user_detail.html / users_import.html
│   ├── events_list.html / event_add.html
│   ├── quiz_list.html / quiz_add.html / quiz_edit.html
│   ├── quiz_detail.html              # Résultats + bouton Relancer
│   ├── quiz_take.html                # Page quiz côté collaborateur
│   ├── quiz_result.html              # Résultat + correction détaillée
│   ├── quiz_invalid.html
│   ├── leaderboard.html              # Page publique (écran TV)
│   ├── regles.html                   # Règles du jeu (page publique)
│   ├── settings.html
│   └── emails/
│       ├── event_notification.html
│       └── quiz_invitation.html
└── static/
    ├── style.css           # Thème DSI (bleu marine #1E3A5F)
    └── app.js
```

---

## Types d'événements pré-chargés

### Bonus
| Code | Événement | Points |
|------|-----------|--------|
| MAIL_SUSPECT | Signalement mail suspect | +15 |
| MAIL_CONFIRME | Mail malveillant confirmé | +25 |
| PHISHING_RATE | Phishing simulé non cliqué | +20 |
| QUIZ_REUSSI | Quiz cybersécurité réussi (≥ seuil) | +30 |
| PORTAIL_SI | Demande via portail SI | +10 |
| COFFRE_MDP | Preuve gestionnaire de mots de passe | +40 (1×/user) |
| FORMATION | Participation à une formation | +50 |
| PARRAINAGE | Parrainage d'un collègue | +20 |

### Malus
| Code | Événement | Points |
|------|-----------|--------|
| SESSION_OUVERTE | Session Windows déverrouillée | −20 |
| PHISHING_CLIC | Clic sur lien phishing simulé | −30 |
| PHISHING_CREDS | Identifiants saisis sur phishing simulé | −50 |
| MDP_EXPIRE | Mot de passe AD expiré >48h | −15 |
| COMPTE_VERROUILLE | Compte AD verrouillé | −10 |
| SESSION_NUIT | Session active nuit/weekend | −10 |
| LOGICIEL_HORS_SI | Logiciel installé hors processus SI | −25 |

---

## Règles métier

- **Score plancher à 0** — jamais négatif
- **Niveaux** : Débutant (0–99) / Vigilant (100–299) / Gardien (300–599) / Expert (600+)
- **COFFRE_MDP** : attribuable une seule fois par utilisateur
- **Annulation** : recrédite les points, recalcule le score depuis zéro, envoie un email
- **Testeurs** : les utilisateurs marqués `is_tester=1` sont exclus du leaderboard et peuvent prévisualiser les quizzes sans que des points soient attribués
- **Emails** : erreurs SMTP loggées en base mais non bloquantes

---

## Import CSV

```bash
# Prévisualisation sans modification
python3 import_users.py utilisateurs.csv --dry-run

# Import réel
python3 import_users.py utilisateurs.csv
```

Format :
```
ad_login,nom,prenom,email
jdupont,DUPONT,Jean,j.dupont@maboite.fr
```

---

## Intégration GoPhish (webhook)

```
POST /api/events/add
Content-Type: application/json

{
  "ad_login": "jdupont",
  "event_code": "PHISHING_CLIC",
  "raison": "Campagne Phishing Q1 2025"
}
```

---

## Snapshot leaderboard (progression quotidienne)

Planifiez un snapshot chaque nuit via cron :

```bash
# crontab -e
0 23 * * * /opt/cyberscore/venv/bin/python3 -c "import sys; sys.path.insert(0,'/opt/cyberscore'); import database; database.snapshot_leaderboard()"
```

Ou manuellement : **Paramètres > Snapshot leaderboard**.

---

## Stack technique

- [Flask](https://flask.palletsprojects.com/) — Framework web Python
- [Waitress](https://docs.pylonsproject.org/projects/waitress/) — Serveur WSGI production
- SQLite — Base de données locale (aucun serveur requis)
- smtplib — Envoi SMTP natif Python
- HTML/CSS/JS vanilla — Interface sans framework frontend
