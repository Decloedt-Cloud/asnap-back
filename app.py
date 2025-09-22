import json
import re
import fitz  # PyMuPDF for PDF text extraction
import smtplib
import os
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from typing import Dict, Any
import logging
from rules import InsuranceAnalyzer
from datetime import datetime
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("assurance-ia")

# Load environment variables
load_dotenv()

# SMTP configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Qwen API configuration
QWEN_API_URL = "https://msie-trustee-approval-importantly.trycloudflare.com/v1/chat/completions"


# Initialize FastAPI
app = FastAPI(title="📊 Assurance IA - Benchmarking API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def extract_company_name(text: str) -> str:
    """Extract insurance company name from PDF text."""

    # Dictionnaire des compagnies avec leurs variantes possibles
    company_patterns = {
        "Assura": [
            r"(?i)\bAssura\b", r"(?i)\bComplementa\b", r"(?i)\bOptima\b",
            r"(?i)\bHospita\b", r"(?i)\bPrevisa\b", r"(?i)\bNatura\b",
            r"(?i)\bMedia\b", r"(?i)\bDenta Plus\b", r"(?i)\bMondia Plus\b"
        ],
        "CSS": [
            r"(?i)\bCSS\b", r"(?i)\bCSS Assurance\b", r"(?i)\bCSS Insurance\b",
            r"(?i)\bMyFlex\b", r"(?i)\bTop\b", r"(?i)\bPremium\b", r"(?i)\bStar\b"
        ],
        "Helsana": [
            r"(?i)\bHelsana\b", r"(?i)\bCOMPLETA\b", r"(?i)\bSANA\b",
            r"(?i)\bOPTIMA\b", r"(?i)\bHelsana Assurances\b"
        ],
        "SWICA": [
            r"(?i)\bSWICA\b", r"(?i)\bCOMPLETA\b", r"(?i)\bOPTIMA\b",
            r"(?i)\bPRIMEO\b", r"(?i)\bSWICA Assurance\b"
        ],
        "KPT": [
            r"(?i)\bKPT\b", r"(?i)\bKPT Assurance\b", r"(?i)\bKrankenkasse KPT\b",
            r"(?i)\bKPT/CPT\b"
        ],
        "Groupe Mutuel": [
            r"(?i)\bGroupe Mutuel\b", r"(?i)\bMutuel\b", r"(?i)\bAMB\b",
            r"(?i)\bEasy Sana\b", r"(?i)\bOptima\b"
        ],
        "Concordia": [
            r"(?i)\bConcordia\b", r"(?i)\bConcordia Assurance\b"
        ],
        "Sanitas": [
            r"(?i)\bSanitas\b", r"(?i)\bSanitas Assurance\b"
        ],
        "Visana": [
            r"(?i)\bVisana\b", r"(?i)\bVisana Assurance\b"
        ],
        "Atupri": [
            r"(?i)\bAtupri\b", r"(?i)\bAtupri Assurance\b"
        ]
    }

    # Recherche dans le texte
    for company, patterns in company_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text):
                logger.info(f"Compagnie détectée: {company} (motif: {pattern})")
                return company.lower()

    # Si aucune compagnie spécifique n'est trouvée, essayer de détecter d'autres indices
    generic_patterns = [
        r"(?i)Caisse[- ]maladie",
        r"(?i)Assurance[- ]maladie",
        r"(?i)Krankenkasse",
        r"(?i)Versicherung"
    ]

    for pattern in generic_patterns:
        if re.search(pattern, text):
            logger.info("Compagnie générique détectée")
            return "generic"

    logger.warning("Aucune compagnie d'assurance détectée dans le texte")
    return "unknown"
def normalize_extracted_data(data: Dict) -> Dict:
    """Normalize Qwen's extracted data to match rules.py expectations."""

    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            if isinstance(value, (int, float)):
                return float(value)

            # Handle percentage strings or currency
            if isinstance(value, str):
                cleaned_value = re.sub(r'[^\d.]', '', value)
                if cleaned_value:
                    return float(cleaned_value)

                # Handle textual representations
                if "cent" in value.lower() or "hundred" in value.lower():
                    return 100.0
                if "cinquante" in value.lower() or "fifty" in value.lower():
                    return 50.0

        except (ValueError, TypeError):
            logger.error(f"Conversion error for value: {value}")
        return default

    def to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value > 0

        if isinstance(value, str):
            value = value.lower().strip()
            true_values = ['true', 'oui', 'yes', '1', 'vrai', 'inclu', 'couv', 'inclus', 'couvert', 'incluse', 'fourni',
                           'disponible']
            return any(keyword in value for keyword in true_values)

        return False

    def to_str(value: Any, valid_values: list, default: str) -> str:
        value = str(value).lower().strip()
        for valid in valid_values:
            if valid in value:
                return valid
        return default

    normalized = {
        "medecine_naturelle": {
            "etendue": to_float(data.get("medecine_naturelle", {}).get("etendue", "0")),
            "plafond": to_float(data.get("medecine_naturelle", {}).get("plafond", "0")),
            "franchise": to_float(data.get("medecine_naturelle", {}).get("franchise", "0"))
        },
        "hospitalisation": {
            "type": to_str(data.get("hospitalisation", {}).get("type", "commune"),
                           ["privé", "semi-privé", "commune"], "commune"),
            "etendue": to_float(data.get("hospitalisation", {}).get("etendue", "0")),
            "franchise": to_float(data.get("hospitalisation", {}).get("franchise", "0"))
        },
        "voyage": {
            "traitement_urgence": to_bool(data.get("voyage", {}).get("traitement_urgence", "false")),
            "rapatriement": to_bool(data.get("voyage", {}).get("rapatriement", "false")),
            "annulation": to_bool(data.get("voyage", {}).get("annulation", "false"))
        },
        "ambulatoire": {
            "prestations": {
                key: to_str(data.get("ambulatoire", {}).get("prestations", {}).get(key, "limité"),
                            ["illimité", "limité"], "limité")
                for key in ["lunettes", "psychotherapie", "medicaments_hors_liste", "transport", "sauvetage"]
            },
            "participation": to_float(data.get("ambulatoire", {}).get("participation", "0"))
        },
        "accident": {
            "clinique_privee": to_bool(data.get("accident", {}).get("clinique_privee", "false")),
            "prestations_supplementaires": to_bool(
                data.get("accident", {}).get("prestations_supplementaires", "false")),
            "capital_deces_invalidite": to_bool(data.get("accident", {}).get("capital_deces_invalidite", "false"))
        },
        "dentaire": {
            "etendue": to_float(data.get("dentaire", {}).get("etendue", "0")),
            "plafond": to_float(data.get("dentaire", {}).get("plafond", "0")),
            "franchise": to_float(data.get("dentaire", {}).get("franchise", "0")),
            "orthodontie": to_float(data.get("dentaire", {}).get("orthodontie", "0"))
        },
        "birth_date": str(data.get("birth_date", "2000-01-01")),
        "compagnie": data.get("compagnie", "unknown"),
    }
    logger.info(f"Normalized data: {json.dumps(normalized, indent=2, ensure_ascii=False)}")
    return normalized


def extract_text_with_qwen(pdf_bytes: bytes) -> Dict:
    """Extract structured information using Qwen API with fallback handling"""
    text = ""
    try:
        # Extract text from PDF first
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join([page.get_text("text") or "" for page in doc])
        logger.info("Texte extrait du PDF avec succès. Sample: %s...", text[:500])
        company_name = extract_company_name(text)
        logger.info(f"Compagnie détectée: {company_name}")

        # Detect insurance provider keywords
        provider_keywords = {
            "Assura": ["Assura", "Complementa", "Optima", "Hospita", "Previsa", "Natura", "Media", "Denta Plus",
                       "Mondia Plus"],
            "CSS": ["CSS", "Top", "Premium", "Star"],
            "Helsana": ["Helsana", "COMPLETA", "SANA", "OPTIMA"],
            "SWICA": ["SWICA", "COMPLETA", "OPTIMA", "PRIMEO"]
        }

        detected_provider = "Generic"
        for provider, keywords in provider_keywords.items():
            if any(keyword in text for keyword in keywords):
                detected_provider = provider
                break

        logger.info(f"Detected insurance provider: {detected_provider}")

        prompt = """Tu es un expert en extraction de données d’assurance santé suisse. Ton objectif est d'extraire les informations de couverture d’un contrat d’assurance (PDF) et de les structurer dans un format JSON conforme aux règles définies.

#### 📁 Structure Attendue du Résultat
```json
{
  "medecine_naturelle": {
    "etendue": [nombre],         // % ou CHF/séance
    "plafond": [nombre],         // nombre de séances par an
    "franchise": [nombre]        // CHF
  },
  "hospitalisation": {
    "type": "[privé | semi-privé | commune]",   // déduit à partir des mots-clés
    "etendue": [nombre],         // % ou CHF/jour
    "franchise": [nombre]        // CHF
  },
  "voyage": {
    "traitement_urgence": [bool],   // true si mention de "traitement d'urgence"
    "rapatriement": [bool],         // true si mention de "rapatriement"
    "annulation": [bool]            // true si mention de "assurance annulation"
  },
  "ambulatoire": {
    "prestations": {
      "lunettes": "[illimité | limité]",
      "psychotherapie": "[illimité | limité]",
      "medicaments_hors_liste": "[illimité | limité]",
      "transport": "[illimité | limité]",
      "sauvetage": "[illimité | limité]"
    },
    "participation": [nombre]     // % (ex: 10)
  },
  "accident": {
    "clinique_privee": [bool],      // true si mention de "clinique privée"
    "prestations_supplementaires": [bool],   // true si mention de "décès", "invalidité"
    "capital_deces_invalidite": [bool]        // true si mention de "CHF X pour décès"
  },
  "dentaire": {
    "etendue": [nombre],               // %
    "plafond": [nombre],               // CHF
    "franchise": [nombre],             // CHF
    "orthodontie": [nombre]            // CHF (si >10'000) ou 0 (enfant <12 ans)
  },
  "birth_date": "[YYYY-MM-DD]",       // pour calculer l'âge de l'assuré
  "compagnie": "{company_name}"
}

        """

        payload = {
            "model": "qwen",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Extrait les données au format JSON sans commentaires"}
            ],
            "temperature": 0.1,
            "max_tokens": 2048
        }

        logger.info("Envoi de la requête à Qwen API...")
        response = requests.post(QWEN_API_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=120)
        response.raise_for_status()

        result = response.json()
        extracted_content = result['choices'][0]['message']['content']
        logger.info(f"Raw Qwen output: {extracted_content}")

        # Extract JSON from response
        start_idx = extracted_content.find('{')
        end_idx = extracted_content.rfind('}') + 1
        if start_idx == -1 or end_idx == 0:
            logger.error(f"No JSON object found in output: {extracted_content}")
            return {}

        json_str = extracted_content[start_idx:end_idx]

        # Clean JSON string
        json_str = re.sub(r'/\*.*?\*/', '', json_str)  # Remove comments
        json_str = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)  # Remove line comments
        logger.info(f"Cleaned JSON string: {json_str}")

        parsed_json = json.loads(json_str)
        return normalize_extracted_data(parsed_json)

    except Exception as e:
        logger.error(f"Extraction error with Qwen: {e}\n{traceback.format_exc()}")
        return {}

# Send email to the user
def send_email_to_user(user_email: str, file_name: str, analysis: 'InsuranceAnalysis'):
    try:
        rows = []
        for result in analysis.categories:
            color_class = result.color.lower()
            rows.append(f'''
                <tr style="border-bottom: 1px solid #f0f0f0;">
                    <td style="padding: 15px 20px; font-weight: 500; color: #2c3e50; vertical-align: middle;">{result.name}</td>
                    <td style="padding: 15px 20px; text-align: center; vertical-align: middle;">
                        <span class="{color_class}" style="display: inline-block; padding: 8px 16px; border-radius: 20px; font-weight: 600; font-size: 14px;">
                            {result.color.upper()}
                        </span>
                    </td>
                </tr>
            ''')

        rows_html = "".join(rows)

        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = SMTP_EMAIL
        msg["To"] = user_email
        msg["Subject"] = "✨ Votre analyse d'assurance est prête"
        with open('email_user_style.css', 'r') as css_file:
            user_css = css_file.read()
        html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{user_css}</style>
</head>
<body>
    <div class="email-container">
        <div class="header">
            <div class="logo">ASNAP</div>
            <div class="tagline">Votre sérénité, en un clic</div>
        </div>

        <div class="content">
            <div class="greeting">Bonjour ! 👋</div>

            <p class="intro-text">
                Nous avons terminé l'analyse de votre police d'assurance <strong>{file_name}</strong>. 
                Voici un résumé détaillé de votre couverture actuelle.
            </p>

            <div class="result-badge">
                🏅 Niveau de couverture : {analysis.overall_medal}
            </div>

            <div class="section-title">
                📊 Détail par catégorie
            </div>

            <table class="results-table">
                <thead>
                    <tr>
                        <th>Catégorie de couverture</th>
                        <th style="text-align: center;">Évaluation</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <div class="rectifier-section">
                <div class="rectifier-title">Des questions sur ces résultats ?</div>
                <div class="rectifier-description">
                    Si vous pensez qu'il y a une erreur dans l'analyse ou souhaitez apporter des précisions, 
                    vous pouvez soumettre une correction.
                </div>
                <a href="https://83.228.199.223/upload-pdf" class="rectifier-btn">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M14,2H6A2,2 0 0,0 4,4V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V8L14,2M18,20H6V4H13V9H18V20Z"/>
                    </svg>
                    Rectifier les résultats
                </a>
            </div>
        </div>

        <div class="footer">
            <div class="footer-content">
                <div style="font-size: 18px; font-weight: 600; margin-bottom: 15px;">ASNAP</div>
                <div style="margin-bottom: 15px; font-size: 14px; opacity: 0.9;">
                    Votre partenaire de confiance pour l'assurance
                </div>
            </div>
            <div class="contact-info">
                📍 Museumstrasse 1, 8021 Zürich<br>
                📧 <a href="mailto:info@asnap.ch">info@asnap.ch</a> | 
                🎧 <a href="mailto:clients@asnap.ch">clients@asnap.ch</a>
            </div>
        </div>
    </div>
</body>
</html>
"""
        msg.add_alternative(html_content, subtype="html")

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info(f"Email envoyé à l'utilisateur : {user_email}")
    except Exception as e:
        logger.error(f"Erreur envoi email utilisateur : {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Erreur envoi email utilisateur : {e}")


def send_email_to_admin(user_email: str, phone: str, file_name: str, analysis: 'InsuranceAnalysis'):
    try:
        rows = []
        for result in analysis.categories:
            color_class = result.color.lower()
            rows.append(f'''
                <tr style="border-bottom: 1px solid #f0f0f0;">
                    <td style="padding: 12px 15px; font-weight: 500; color: #2c3e50; vertical-align: middle;">{result.name}</td>
                    <td style="padding: 12px 15px; text-align: center; vertical-align: middle;">
                        <span class="{color_class}" style="display: inline-block; padding: 6px 12px; border-radius: 15px; font-weight: 600; font-size: 12px;">
                            {result.color.upper()}
                        </span>
                    </td>
                </tr>
            ''')

        rows_html = "".join(rows)

        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = SMTP_EMAIL
        msg["To"] = "proiadev@gmail.com"
        msg["Subject"] = "🔔 Nouveau benchmark ASNAP"
        with open('email_admin_style.css', 'r') as css_file:
            admin_css = css_file.read()
        html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{admin_css}</style>
</head>
<body>
    <div class="email-container">
        <div class="admin-header">
            <h1>🔔 Nouveau Benchmark</h1>
            <div class="subtitle">Administration ASNAP</div>
        </div>

        <div class="content">
            <div class="user-info">
                <h3>👤 Informations Utilisateur</h3>
                <div class="info-row">
                    <span class="info-label">📧 Email :</span>
                    <span class="info-value">{user_email}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📞 Téléphone :</span>
                    <span class="info-value">{phone}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📄 Fichier :</span>
                    <span class="info-value">{file_name}</span>
                </div>
            </div>

            <div class="result-summary">
                <div style="color: #2c3e50; font-weight: 600; margin-bottom: 10px;">Résultat Global</div>
                <div class="result-badge">🏅 {analysis.overall_medal}</div>
            </div>

            <table class="results-table">
                <thead>
                    <tr>
                        <th>Catégorie</th>
                        <th style="text-align: center;">Évaluation</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="footer">
            <div>ASNAP Administration Panel</div>
            <div class="timestamp">Email généré automatiquement</div>
        </div>
    </div>
</body>
</html>
"""
        msg.add_alternative(html_content, subtype="html")

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info("Email envoyé à l'administrateur.")
    except Exception as e:
        logger.error(f"Erreur envoi email admin : {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Erreur envoi email admin : {e}")

# API endpoint: Analyze PDF and generate benchmark report
@app.post("/api/upload/")
async def upload_pdf(
    file: UploadFile = File(...),
    email: str = Form(...),
    phone: str = Form(...),
    optional_categories: str = Form("{}")
):
    try:
        logger.info(f"Starting processing for {email}")

        if not file or not email or not phone:
            raise HTTPException(status_code=400, detail="Missing PDF file, email, or phone.")

        pdf_bytes = await file.read()
        logger.info(f"Received file {file.filename}, size: {len(pdf_bytes)} bytes.")

        # Extract structured data
        structured_data = extract_text_with_qwen(pdf_bytes)
        if not structured_data:
            logger.warning("No structured data extracted, using fallback.")
            structured_data = {"compagnie": "unknown", "birth_date": "2000-01-01"}

        # Analyze with rules.py
        analyzer = InsuranceAnalyzer()
        try:
            analysis = analyzer.analyze_pdf(structured_data)
            logger.info(f"Analysis completed. Overall medal: {analysis.overall_medal}")
        except Exception as e:
            logger.error(f"Analyzer error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="Error analyzing PDF data")

        # Handle optional categories
        facultatives = json.loads(optional_categories)
        exclusions = []
        if facultatives.get("accident", False):
            exclusions.append("Accident")
        if facultatives.get("naturalMedicine", False):
            exclusions.append("Médecine naturelle")
        if facultatives.get("travelInsurance", False):
            exclusions.append("Voyage")

        if exclusions:
            logger.info(f"Applying exclusions: {exclusions}")
            try:
                analysis = analyzer.rectify_analysis(exclusions)
                logger.info(f"Rectified analysis. New medal: {analysis.overall_medal}")
            except Exception as e:
                logger.error(f"Rectify analysis error: {e}\n{traceback.format_exc()}")
                raise HTTPException(status_code=500, detail="Error rectifying analysis")

        # Send emails (non-blocking)
        try:
            send_email_to_user(email, file.filename, analysis)
        except Exception as e:
            logger.warning(f"Failed to send user email: {e}")

        try:
            send_email_to_admin(email, phone, file.filename, analysis)
        except Exception as e:
            logger.warning(f"Failed to send admin email: {e}")

        # Return JSON response
        return {
            "message": "Analysis complete, emails sent",
            "benchmark": {
                "final_score": analysis.overall_medal,
                "detailed_scores": {r.name: {"color": r.color, "details": r.details} for r in analysis.categories}
            },
            "extracted_data": structured_data
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in /upload/ endpoint: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")

# Health check endpoint
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "message": "API is running with Qwen integration"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True, log_level="debug")