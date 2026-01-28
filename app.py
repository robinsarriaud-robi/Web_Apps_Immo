import streamlit as st
import requests
import json
import os
from datetime import datetime
from PIL import Image
from pydantic import BaseModel, Field
from typing import Optional, Literal
import google.generativeai as genai

# --- 1. CONFIGURATION & MODELS ---

st.set_page_config(page_title="Immo-Tracker AI", page_icon="🏠", layout="wide")

# Modèle de données strict (Pydantic)
class ImmoData(BaseModel):
    date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    ville: str = Field(default="")
    quartier: str = Field(default="")
    prix: float = Field(default=0.0)
    surface: float = Field(default=0.0)
    type_vendeur: Literal["Agence", "Particulier", "Autre"] = Field(default="Agence")
    email: Optional[str] = Field(default="")
    telephone: Optional[str] = Field(default="")
    
    # Champs système (non extraits par l'IA)
    url: Optional[str] = ""
    status: Literal["Non", "A contacter", "Contacté"] = "A contacter"
    commentaire: Optional[str] = ""
    create_draft: bool = False
    message_draft: Optional[str] = ""

# --- 2. FONCTIONS BACKEND ---

def process_images(uploaded_files):
    """Conversion images pour Gemini (PIL)"""
    processed_images = []
    for uploaded_file in uploaded_files:
        try:
            image = Image.open(uploaded_file)
            if image.mode != "RGB":
                image = image.convert("RGB")
            processed_images.append(image)
        except Exception as e:
            st.error(f"Erreur lecture image: {e}")
    return processed_images

def fetch_url_content(url):
    """Scraping HTML via Requests (Remplace le proxy JS)"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text[:30000] # Limite pour les tokens
    except Exception as e:
        st.warning(f"Scraping direct échoué ({e}). Utilisation de l'URL seule.")
        return None

def analyze_with_gemini(api_key, raw_text, url_input, images):
    """ETL : Extraction Transform Load via Gemini"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3-flash-preview')
    
    prompt = [
        """Agis comme un expert immobilier. Extrais les données au format JSON strict :
        {
            "date": "YYYY-MM-DD", "ville": "Ville", "quartier": "Quartier/Métro",
            "prix": Float, "surface": Float, "type_vendeur": "Agence"|"Particulier",
            "email": "email ou vide", "telephone": "tel ou vide"
        }
        Si inconnu, mets 0 ou chaine vide."""
    ]

    if url_input:
        html = fetch_url_content(url_input)
        prompt.append(f"Source HTML : \n{html}" if html else f"Source URL : {url_input}")
    
    if raw_text:
        prompt.append(f"Source Texte : \n{raw_text}")
    
    prompt.extend(images)

    try:
        response = model.generate_content(prompt)
        # Nettoyage JSON robuste
        json_str = response.text.replace("```json", "").replace("```", "").strip()
        start, end = json_str.find('{'), json_str.rfind('}') + 1
        return json.loads(json_str[start:end]) if start != -1 else {}
    except Exception as e:
        st.error(f"Erreur Gemini: {e}")
        return None

def generate_draft_message(api_key, quartier):
    """Génération Template Robin"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Génère un message pour un vendeur immobilier. Remplace [Quartier] par "{quartier}".
    Garde EXACTEMENT ce modèle :
    Bonjour,
    J'ai vu votre annonce pour l'appartement situé [Quartier] et je suis très intéressé.
    Je m'appelle Robin, j'ai 24 ans, ingénieur. Dossier validé (250k€), sans condition suspensive.
    Disponible rapidement pour visiter.
    Cordialement,
    Robin Sarriaud
    0610980100
    robin.sarriaud@gmail.com
    """
    try:
        return model.generate_content(prompt).text
    except Exception:
        return "Erreur génération message."

def send_to_webhook(webhook_url, data):
    """Envoi vers Google Apps Script"""
    try:
        # Requests gère le JSON proprement (pas besoin de no-cors comme en JS)
        resp = requests.post(webhook_url, json=data, headers={'Content-Type': 'application/json'})
        return resp.status_code in [200, 302]
    except Exception as e:
        st.error(f"Erreur Webhook: {e}")
        return False

# --- 3. STATE MANAGEMENT ---

if 'form_data' not in st.session_state:
    st.session_state.form_data = ImmoData().model_dump()

# --- 4. INTERFACE ---

st.title("🏡 Immo-Tracker AI")

# Sidebar : Chargement des secrets
with st.sidebar:
    st.header("⚙️ Config")
    # Chargement silencieux depuis secrets.toml
    try:
        sec_key = st.secrets["GEMINI_API_KEY"]
        sec_url = st.secrets["WEBHOOK_URL"]
        st.success("Secrets chargés.")
    except Exception:
        sec_key, sec_url = "", ""
        st.warning("Aucun secret trouvé.")

    api_key = st.text_input("Gemini API Key", value=sec_key, type="password")
    webhook_url = st.text_input("Webhook URL", value=sec_url)

# Layout
col_in, col_out = st.columns([1, 2], gap="large")

with col_in:
    st.subheader("1. Sources")
    files = st.file_uploader("Images", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])
    url_in = st.text_input("URL Annonce")
    text_in = st.text_area("Texte Brut", height=150)
    
    if st.button("🪄 Analyser", type="primary", use_container_width=True):
        if not api_key:
            st.error("API Key manquante.")
        else:
            with st.spinner("Analyse en cours..."):
                imgs = process_images(files) if files else []
                data = analyze_with_gemini(api_key, text_in, url_in, imgs)
                if data:
                    # Update partiel pour ne pas écraser les champs système
                    st.session_state.form_data.update(data)
                    st.session_state.form_data['url'] = url_in
                    st.rerun()

with col_out:
    if st.session_state.form_data:
        st.subheader("2. Validation & Envoi")
        
        with st.form("main_form"):
            c1, c2 = st.columns(2)
            # Mapping des champs
            st.session_state.form_data['date'] = c1.text_input("Date", st.session_state.form_data['date'])
            st.session_state.form_data['ville'] = c1.text_input("Ville", st.session_state.form_data['ville'])
            st.session_state.form_data['prix'] = c1.number_input("Prix (€)", value=float(st.session_state.form_data['prix']))
            st.session_state.form_data['email'] = c1.text_input("Email", st.session_state.form_data['email'])

            st.session_state.form_data['quartier'] = c2.text_input("Quartier", st.session_state.form_data['quartier'])
            st.session_state.form_data['surface'] = c2.number_input("Surface (m²)", value=float(st.session_state.form_data['surface']))
            st.session_state.form_data['type_vendeur'] = c2.selectbox("Vendeur", ["Agence", "Particulier", "Autre"], index=["Agence", "Particulier", "Autre"].index(st.session_state.form_data.get('type_vendeur', 'Agence')))
            st.session_state.form_data['telephone'] = c2.text_input("Téléphone", st.session_state.form_data['telephone'])

            st.divider()
            
            # Gestion Message
            c_gen, c_txt = st.columns([1, 3])
            if c_gen.form_submit_button("🤖 Générer Msg"):
                msg = generate_draft_message(api_key, st.session_state.form_data['quartier'])
                st.session_state.form_data['message_draft'] = msg
                st.rerun()
            
            st.session_state.form_data['message_draft'] = c_txt.text_area("Message", st.session_state.form_data['message_draft'], height=150)
            
            # Options finales
            cc1, cc2 = st.columns(2)
            st.session_state.form_data['create_draft'] = cc1.toggle("Brouillon Gmail", st.session_state.form_data['create_draft'])
            st.session_state.form_data['status'] = cc2.selectbox("Status", ["Non", "A contacter", "Contacté"], index=["Non", "A contacter", "Contacté"].index(st.session_state.form_data.get('status', 'A contacter')))
            st.session_state.form_data['commentaire'] = st.text_area("Note", st.session_state.form_data['commentaire'], height=68)

            # Submit final
            if st.form_submit_button("🚀 Envoyer vers Sheets", type="primary", use_container_width=True):
                if send_to_webhook(webhook_url, st.session_state.form_data):
                    st.success("Envoyé !")
                    st.balloons()
