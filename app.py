import streamlit as st
import chromadb
from google import genai
from modules.rag_pipeline import rag_pipeline
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

# Hiển thị lịch sử (Dùng format chuẩn của Streamlit)
for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

# Nhập câu hỏi
if user_input := st.chat_input("Nhập câu hỏi..."):
    # Hiển thị và lưu câu hỏi user
    st.chat_message("user").write(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.spinner("Bot đang suy nghĩ..."):
        try:
            # Gọi pipeline từ file rag_pipeline.py
            response = rag_pipeline(user_input, collection, client)
            print(f"Kết quả truy vấn ChromaDB: {response}")  

            # Hiển thị và lưu câu trả lời bot
            st.chat_message("assistant").write(response)
            st.session_state.messages.append({"role": "assistant", "content": response})
        except Exception as e:
            st.error(f"Lỗi: {e}")