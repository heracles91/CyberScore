#!/usr/bin/env python3
# import_users.py — Import en masse d'utilisateurs depuis un fichier CSV
#
# Format CSV attendu (avec en-tête) :
#   ad_login,nom,prenom,email
#   jdupont,DUPONT,Jean,j.dupont@maboite.fr
#   mmartin,MARTIN,Marie,m.martin@maboite.fr
#
# Utilisation :
#   python import_users.py utilisateurs.csv
#   python import_users.py utilisateurs.csv --dry-run

import csv
import sys
import os
import re

# Ajout du répertoire courant au path pour importer database et config
sys.path.insert(0, os.path.dirname(__file__))
import database as db


def is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def import_csv(filepath, dry_run=False):
    db.init_db()

    created = 0
    skipped = 0
    errors  = 0

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        # Vérification des colonnes
        required_cols = {'ad_login', 'nom', 'prenom', 'email'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            print(f"[ERREUR] Le fichier CSV doit contenir les colonnes : {', '.join(required_cols)}")
            print(f"         Colonnes trouvées : {', '.join(reader.fieldnames or [])}")
            sys.exit(1)

        for i, row in enumerate(reader, start=2):  # ligne 2 = première donnée
            ad_login = row.get('ad_login', '').strip().lower()
            nom      = row.get('nom', '').strip().upper()
            prenom   = row.get('prenom', '').strip()
            email    = row.get('email', '').strip().lower()

            # Validation ligne
            line_errors = []
            if not ad_login:
                line_errors.append("login AD vide")
            if not nom:
                line_errors.append("nom vide")
            if not prenom:
                line_errors.append("prénom vide")
            if not is_valid_email(email):
                line_errors.append(f"email invalide ({email!r})")

            if line_errors:
                print(f"[LIGNE {i}] Ignoré — {', '.join(line_errors)} : {dict(row)}")
                errors += 1
                continue

            # Vérification doublon
            existing = db.get_user_by_login(ad_login)
            if existing:
                print(f"[LIGNE {i}] Ignoré — login '{ad_login}' déjà existant.")
                skipped += 1
                continue

            if dry_run:
                print(f"[DRY-RUN] Serait créé : {prenom} {nom} <{email}> ({ad_login})")
                created += 1
            else:
                ok, err = db.create_user(ad_login, nom, prenom, email)
                if ok:
                    print(f"[OK] Créé : {prenom} {nom} <{email}> ({ad_login})")
                    created += 1
                else:
                    print(f"[ERREUR] Ligne {i} — {err}")
                    errors += 1

    print()
    print("─" * 50)
    if dry_run:
        print(f"[DRY-RUN] {created} utilisateurs seraient créés, {skipped} ignorés, {errors} erreurs.")
    else:
        print(f"Résultat : {created} créés, {skipped} ignorés (doublons), {errors} erreurs.")
    print("─" * 50)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python import_users.py <fichier.csv> [--dry-run]")
        print()
        print("Format CSV (avec en-tête) :")
        print("  ad_login,nom,prenom,email")
        print("  jdupont,DUPONT,Jean,j.dupont@maboite.fr")
        sys.exit(1)

    filepath = sys.argv[1]
    dry_run  = "--dry-run" in sys.argv

    if not os.path.isfile(filepath):
        print(f"[ERREUR] Fichier introuvable : {filepath}")
        sys.exit(1)

    print(f"Import depuis : {filepath}")
    if dry_run:
        print("Mode DRY-RUN activé — aucune modification en base.")
    print()

    import_csv(filepath, dry_run=dry_run)
