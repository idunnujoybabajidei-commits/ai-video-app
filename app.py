import streamlit as st
import os
import io
import time
import tempfile
import requests
import subprocess
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
        raise ValueError("Unsupported file type. Please upload PDF, DOCX, or TXT.")
    return text

def chunk_text_into_scenes(text, target_duration_minutes):
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
def generate_clip_via_api(prompt, hf_token, model_name, max_retries=3):
    api_url = f"https://api-inference.huggingface.co/models/{model_name}"
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": prompt}
    for attempt in range(max_retries):
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                return response.content
            elif response.status_code == 429 or response.status_code == 503:
                time.sleep(15)
            else:
                raise Exception(f"API Error {response.status_code}: {response.text}")
        except requests.exceptions.RequestException:
            time.sleep(5)
    return None

# ==========================================
# 4. 30-MINUTE VIDEO STITCHING ENGINE
# ==========================================
def stitch_clips_robust(clip_paths, output_path):
    temp_dir = os.path.dirname(output_path)
    processed_paths = []
    for i, cp in enumerate(clip_paths):
        processed_path = os.path.join(temp_dir, f"proc_{i:04d}.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", cp,
            "-vf", "fade=t=in:st=0:d=0.5,fade=t=out:st=4.5:d=0.5",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", processed_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processed_paths.append(processed_path)
        
    list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in processed_paths:
            safe_path = p.replace('\\', '/')
            f.write(f"file '{safe_path}'\n")
            
    cmd_concat = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
    try:
        subprocess.run(cmd_concat, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        fallback_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", output_path]
        subprocess.run(fallback_cmd, check=True)

# ==========================================
# 5. MAIN APP INTERFACE
# ==========================================
st.title("🎬 Doc-to-Video AI")
st.markdown("Upload a document, enter your free Hugging Face token, and generate a video up to 30 minutes long.")

with st.sidebar:
    st.header("⚙️ Configuration")
    hf_token = st.text_input("Hugging Face Token", type="password", help="Get a free token from huggingface.co/settings/tokens")
    model_name = st.selectbox("Video Model", ["ali-vilab/text-to-video-ms-1.7b", "damo-vilab/text-to-video-ms-1.7b", "cerspense/zeroscope_v2_576w"])
    theme = st.selectbox("UI Theme", ["Dark", "Light"])
    if theme == "Dark":
        st.markdown("<style>.stApp { background-color: #0e1117; color: white; }</style>", unsafe_allow_html=True)
    duration = st.slider("Target Video Length (Minutes)", 1, 30, 3, help="Each minute takes ~12 API calls.")

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
            temp_dir = tempfile.mkdtemp()
            clip_paths = []
            
            for i, scene in enumerate(scenes):
                progress_text.text(f"Generating scene {i+1} of {len(scenes)}...")
                prompt = f"high quality, cinematic, {scene}"
                clip_bytes = generate_clip_via_api(prompt, hf_token, model_name)
                if clip_bytes:
                    clip_path = os.path.join(temp_dir, f"scene_{i:04d}.mp4")
                    with open(clip_path, "wb") as f: f.write(clip_bytes)
                    clip_paths.append(clip_path)
                else:
                    st.warning(f"Failed to generate scene {i+1}. Skipping.")
                pct = int(((i + 1) / len(scenes)) * 90)
                progress_bar.progress(pct)
                
            if clip_paths:
                progress_text.text("🔗 Stitching clips...")
                progress_bar.progress(95)
                final_video_path = os.path.join(temp_dir, "final_documentary.mp4")
                stitch_clips_robust(clip_paths, final_video_path)
                progress_bar.progress(100)
                progress_text.text("✅ Video Generation Complete!")
                st.subheader("🎞️ Final Video")
                with open(final_video_path, "rb") as video_file:
                    video_bytes = video_file.read()
                st.video(video_bytes)
                st.download_button(label="⬇️ Download Video (.mp4)", data=video_bytes, file_name=f"ai_video_{int(time.time())}.mp4", mime="video/mp4")
            else:
                st.error("No clips generated. Check API token.")
        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
