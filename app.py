import streamlit as st
import chromadb
import uuid
from datetime import datetime
from google import genai
from modules.rag_pipeline import rag_pipeline
from modules.rewrite_state import rewrite_query_with_context, save_chat_to_file, select_history_chat 
from ingest.database import VietnameseSBERTEmbedding, setup_database
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os

load_dotenv()

# --- DÙNG CACHE ĐỂ LOAD MODEL 1 LẦN DUY NHẤT ---
@st.cache_resource
def init_resources():
    # Load model embedding (giống hệt bên ingest.py)
    model_sbert = SentenceTransformer("keepitreal/vietnamese-sbert", device="cpu")
    viet_em_fn = VietnameseSBERTEmbedding(model=model_sbert)
    
    # Kết nối DB với embedding function đúng
    collection = setup_database()
    
    # Init Gemini client
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    
    return collection, client

# Lấy các tài nguyên đã được cache
collection, client = init_resources()

st.set_page_config(page_title="MIM Chatbot", page_icon="🎓")
st.title("🎓 Chatbot tư vấn đào tạo MIM")

# Khởi tạo lịch sử chat
if "messages" not in st.session_state:
    st.session_state.messages = []

# 2. Khởi tạo Session ID duy nhất cho lượt truy cập này
if "session_id" not in st.session_state:
    # Tạo ID ngắn gọn (8 ký tự đầu của UUID) để tên file không quá dài
    st.session_state.session_id = f"{datetime.now().strftime('%Y%m%d')}_{str(uuid.uuid4())[:8]}"


LOG_DIR = "history_files"
os.makedirs(LOG_DIR, exist_ok=True)
with st.sidebar:
    st.title("📚 Lịch sử")
    select_history_chat(LOG_DIR)



# Hiển thị lịch sử (Dùng format chuẩn của Streamlit)
for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])
MODEL_NAME = os.getenv("MODEL_NAME")
# Nhập câu hỏi
if user_input := st.chat_input("Nhập câu hỏi..."):
    # Hiển thị và lưu câu hỏi user
    st.chat_message("user").write(user_input)
    rewritten_query = rewrite_query_with_context(user_input, st.session_state.messages, client,MODEL_NAME)
    print(f"Câu hỏi sau khi rewrite: {rewritten_query}")  
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.spinner("Bot đang suy nghĩ..."):
        try:
            # Gọi pipeline từ file rag_pipeline.py
            response = rag_pipeline(rewritten_query, collection, client)
            print(f"Kết quả truy vấn ChromaDB: {response}")  
            save_chat_to_file(user_input, rewritten_query, response,client,LOG_DIR)  
            # Hiển thị và lưu câu trả lời bot
            st.chat_message("assistant").write(response)
            st.session_state.messages.append({"role": "assistant", "content": response})
            if st.session_state.get("has_real_title", True):
                # Nếu đã có tên thật sau khi lưu, rerun để Sidebar cập nhật ngay tiêu đề cuộc chat.
                st.rerun()
        except Exception as e:
            st.error(f"Lỗi: {e}")