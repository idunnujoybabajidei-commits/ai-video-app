import streamlit as st
import os
import io
import time
import tempfile
import requests
import subprocess
import imageio_ffmpeg # New library to provide ffmpeg
from urllib.parse import quote
from pypdf import PdfReader
import docx

# Get the correct path to the ffmpeg executable
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# ==========================================
# 1. MOBILE-RESPONSIVE UI
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
    .log-box {
        background-color: #f0f2f6; border: 1px solid #ccc; border-radius: 5px;
        padding: 10px; height: 150px; overflow-y: auto; font-family: monospace; font-size: 0.8em;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DOCUMENT PARSING
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
# 3. ZERO-CAPITAL IMAGE-TO-VIDEO PIPELINE
# ==========================================
def generate_image_and_motion_clip(prompt, output_path, log_box):
    """Generates an AI image via Pollinations and animates it with a Ken Burns effect."""
    
    # Step 1: Generate Image (100% Free, No Token)
    img_url = f"https://image.pollinations.ai/prompt/{quote(prompt)}"
    log_box.text("🖼️ Downloading AI image...")
    
    try:
        response = requests.get(img_url, timeout=60)
        if response.status_code != 200:
            return False
        img_path = output_path.replace(".mp4", ".jpg")
        with open(img_path, "wb") as f:
            f.write(response.content)
    except Exception:
        return False

    # Step 2: Animate Image (Ken Burns Zoom + Fade) using imageio_ffmpeg
    log_box.text("🎥 Applying cinematic motion...")
    cmd = [
        FFMPEG_PATH, "-y", "-i", img_path,
        "-vf", "scale=800:-1,zoompan=z='min(zoom+0.0015,1.5)':d=125:s=720x480:fps=25,fade=t=in:st=0:d=0.5,fade=t=out:st=4.5:d=0.5",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest", output_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

# ==========================================
# 4. VIDEO STITCHING ENGINE
# ==========================================
def stitch_clips_robust(clip_paths, output_path, log_box):
    temp_dir = os.path.dirname(output_path)
    list_path = os.path.join(temp_dir, "concat_list.txt")
    
    log_box.text("🔗 Stitching final video together...")
    with open(list_path, "w") as f:
        for p in clip_paths:
            safe_path = p.replace('\\', '/')
            f.write(f"file '{safe_path}'\n")
            
    cmd_concat = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
    try:
        subprocess.run(cmd_concat, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        log_box.text("⚠️ Re-encoding for compatibility...")
        fallback_cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", output_path]
        subprocess.run(fallback_cmd, check=True)

# ==========================================
# 5. MAIN APP INTERFACE
# ==========================================
st.title("🎬 Doc-to-Video AI")
st.markdown("Upload a document to generate a cinematic video. **No API token required!**")

with st.sidebar:
    st.header("⚙️ Configuration")
    theme = st.selectbox("UI Theme", ["Dark", "Light"])
    if theme == "Dark":
        st.markdown("<style>.stApp { background-color: #0e1117; color: white; }</style>", unsafe_allow_html=True)
    duration = st.slider("Target Video Length (Minutes)", 1, 30, 1, help="1 min = ~12 scenes.")

uploaded_file = st.file_uploader("📄 Upload Document (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"])

if uploaded_file is not None and st.button("🚀 Generate Video", type="primary"):
    try:
        with st.spinner("Parsing document..."):
            text = extract_text_from_file(uploaded_file)
            scenes = chunk_text_into_scenes(text, duration)
            st.success(f"Extracted {len(scenes)} scenes!")
        
        st.subheader("📡 AI Generation Pipeline")
        progress_text = st.empty()
        progress_bar = st.progress(0)
        log_box = st.empty()
        temp_dir = tempfile.mkdtemp()
        clip_paths = []
        
        for i, scene in enumerate(scenes):
            progress_text.text(f"Generating scene {i+1} of {len(scenes)}...")
            
            # Enhance prompt
            prompt = f"cinematic lighting, 4k resolution, highly detailed, {scene}"
            clip_path = os.path.join(temp_dir, f"scene_{i:04d}.mp4")
            
            success = generate_image_and_motion_clip(prompt, clip_path, log_box)
            
            if success:
                clip_paths.append(clip_path)
                log_box.text(f"✅ Scene {i+1} generated successfully!")
            else:
                st.warning(f"Scene {i+1} failed. Skipping.")
            
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
            st.error("No clips were generated. Check your internet connection.")
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
