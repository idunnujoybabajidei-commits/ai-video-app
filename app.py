import streamlit as st
import os
import io
import time
import tempfile
import requests
import subprocess
import datetime
from pypdf import PdfReader
import docx

# ==========================================
# 1. MOBILE-RESPONSIVE UI & CONFIGURATION
# ==========================================
st.set_page_config(page_title="AI Video Generator", page_icon="🎬", layout="centered")

st.markdown("""
<style>
    .stApp { max-width: 100%; padding: 15px; }
    .stButton>button { width: 100%; border-radius: 8px; height: 3em; font-weight: bold; font-size: 1.1em; }
    [data-testid="stSidebar"] { min-width: 250px; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stProgress > div > div > div > div { background-color: #FF4B4B; }
    /* Custom log box for mobile */
    .log-box {
        background-color: #f0f2f6;
        border: 1px solid #ccc;
        border-radius: 5px;
        padding: 10px;
        height: 150px;
        overflow-y: auto;
        font-family: monospace;
        font-size: 0.8em;
        color: #333;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DOCUMENT PARSING & SCRIPT GENERATION
# ==========================================
def extract_text_from_file(uploaded_file):
    file_type = uploaded_file.name.split('.')[-1].lower()
    text = ""
    if file_type == 'pdf':
        pdf_reader = PdfReader(uploaded_file)
        for page in pdf_reader.pages:
            extracted = page.extract_text()
            if extracted: text += extracted + "\n"
    elif file_type == 'docx':
        doc = docx.Document(io.BytesIO(uploaded_file.read()))
        for para in doc.paragraphs:
            if para.text.strip(): text += para.text + "\n"
    elif file_type == 'txt':
        text = uploaded_file.read().decode('utf-8')
    else:
        raise ValueError("Unsupported file type.")
    return text

def chunk_text_into_scenes(text, target_duration_minutes):
    """Chunks text into scenes. ~12 scenes per minute = 5 seconds per scene."""
    words = text.split()
    if not words: return []
    total_scenes_needed = target_duration_minutes * 12
    words_per_scene = max(15, len(words) // total_scenes_needed)
    scenes = []
    for i in range(0, len(words), words_per_scene):
        chunk = " ".join(words[i:i + words_per_scene])
        scenes.append(chunk)
    return scenes

# ==========================================
# 3. ZERO-CAPITAL VIDEO PIPELINE (API)
# ==========================================
def generate_clip_via_api(prompt, hf_token, model_name, log_box):
    # Using Hugging Face Router API (higher success rate for free tier)
    api_url = f"https://router.huggingface.co/hf-inference/models/{model_name}"
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": prompt}
    
    max_wait_time = 180 # 3 minutes
    waited = 0
    
    while waited < max_wait_time:
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=120)
            
            if response.status_code == 200:
                return response.content
            elif response.status_code == 503:
                log_box.text(f"⏳ Model loading... waiting 20s.")
                time.sleep(20)
                waited += 20
            elif response.status_code == 429:
                return "RATE_LIMIT"
            elif response.status_code == 401 or response.status_code == 403:
                return "AUTH_ERROR"
            else:
                return f"API_ERROR_{response.status_code}"
        except requests.exceptions.RequestException:
            time.sleep(5)
            waited += 5
            
    return "TIMEOUT"

# ==========================================
# 4. BULLETPROOF VIDEO STITCHING ENGINE
# ==========================================
def stitch_clips_robust(clip_paths, output_path, log_box):
    """
    Normalizes clips, adds silent audio, applies fades, and concatenates.
    This prevents all FFmpeg errors on mobile browsers and varying video lengths.
    """
    temp_dir = os.path.dirname(output_path)
    processed_paths = []
    
    # Step 1: Normalize each clip
    for i, cp in enumerate(clip_paths):
        processed_path = os.path.join(temp_dir, f"proc_{i:04d}.mp4")
        log_box.text(f"🎬 Processing clip {i+1}/{len(clip_paths)}...")
        
        # -r 24 (force 24fps), add silent audio track for iOS compatibility, 0.5s fades
        cmd = [
            "ffmpeg", "-y", "-i", cp,
            "-vf", "fade=t=in:st=0:d=0.5,fade=t=out:st=4.5:d=0.5,fps=24",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            processed_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processed_paths.append(processed_path)
        
    # Step 2: Concatenate
    log_box.text("🔗 Stitching final video together...")
    list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in processed_paths:
            safe_path = p.replace('\\', '/')
            f.write(f"file '{safe_path}'\n")
            
    cmd_concat = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
    try:
        subprocess.run(cmd_concat, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        # Fallback re-encode if codecs mismatch
        log_box.text("⚠️ Codec mismatch detected. Re-encoding final video...")
        fallback_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", output_path]
        subprocess.run(fallback_cmd, check=True)

# ==========================================
# 5. MAIN APP INTERFACE
# ==========================================
st.title("🎬 Doc-to-Video AI")
st.markdown("Upload a document, enter your free HF token, and generate a cinematic video.")

with st.sidebar:
    st.header("⚙️ Configuration")
    hf_token = st.text_input("Hugging Face Token", type="password", help="Get a free token from huggingface.co/settings/tokens")
    theme = st.selectbox("UI Theme", ["Dark", "Light"])
    if theme == "Dark":
        st.markdown("<style>.stApp { background-color: #0e1117; color: white; }</style>", unsafe_allow_html=True)
    duration = st.slider("Target Video Length (Minutes)", 1, 30, 1, help="1 min = ~12 API calls. Start small!")

uploaded_file = st.file_uploader("📄 Upload Document (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"])

if uploaded_file is not None and st.button("🚀 Generate Video", type="primary"):
    if not hf_token:
        st.error("Please enter your Hugging Face token in the sidebar.")
    else:
        try:
            with st.spinner("Parsing document..."):
                text = extract_text_from_file(uploaded_file)
                scenes = chunk_text_into_scenes(text, duration)
                st.success(f"Extracted {len(scenes)} scenes!")
            
            st.subheader("📡 AI Generation Pipeline")
            progress_text = st.empty()
            progress_bar = st.progress(0)
            log_box = st.empty() # Real-time console output
            temp_dir = tempfile.mkdtemp()
            clip_paths = []
            
            models_to_try = [
                "ali-vilab/text-to-video-ms-1.7b",
                "damo-vilab/text-to-video-ms-1.7b",
                "cerspense/zeroscope_v2_576w"
            ]
            
            stop_app = False
            
            for i, scene in enumerate(scenes):
                progress_text.text(f"Generating scene {i+1} of {len(scenes)}...")
                
                # Enhance prompt for better video quality
                prompt = f"cinematic lighting, 4k resolution, highly detailed, {scene}"
                
                clip_bytes = None
                for model_name in models_to_try:
                    log_box.text(f"Trying model: {model_name.split('/')[-1]}...")
                    clip_bytes = generate_clip_via_api(prompt, hf_token, model_name, log_box)
                    
                    if clip_bytes == "AUTH_ERROR":
                        st.error("Invalid Hugging Face Token. Check token and try again.")
                        stop_app = True
                        break
                    elif clip_bytes == "RATE_LIMIT":
                        log_box.text(f"⚠️ Rate limited on {model_name.split('/')[-1]}. Trying next...")
                        continue
                    elif clip_bytes and not isinstance(clip_bytes, str) and clip_bytes != "TIMEOUT":
                        clip_path = os.path.join(temp_dir, f"scene_{i:04d}.mp4")
                        with open(clip_path, "wb") as f: f.write(clip_bytes)
                        clip_paths.append(clip_path)
                        log_box.text(f"✅ Scene {i+1} generated successfully!")
                        break
                        
                if stop_app:
                    st.stop()
                    break
                    
                if not clip_paths or (isinstance(clip_bytes, str) and clip_bytes in ["TIMEOUT", "RATE_LIMIT"]):
                    st.warning(f"Scene {i+1} failed (Servers busy). Skipping.")
                
                pct = int(((i + 1) / len(scenes)) * 90)
                progress_bar.progress(pct)
                
            if clip_paths:
                progress_text.text("🔗 Stitching clips together with smooth transitions...")
                progress_bar.progress(95)
                final_video_path = os.path.join(temp_dir, "final_documentary.mp4")
                
                stitch_clips_robust(clip_paths, final_video_path, log_box)
                
                progress_bar.progress(100)
                progress_text.text("✅ Video Generation Complete!")
                log_box.empty()
                
                st.subheader("🎞️ Final Video")
                with open(final_video_path, "rb") as video_file:
                    video_bytes = video_file.read()
                st.video(video_bytes)
                st.download_button(label="⬇️ Download Video (.mp4)", data=video_bytes, file_name=f"ai_video_{int(time.time())}.mp4", mime="video/mp4")
            else:
                st.error("No clips generated. HF API may be overloaded. Try again in 30 mins.")
        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
