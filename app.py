import streamlit as st
import chromadb
import uuid
import json
import pandas as pd
from datetime import datetime
from google import genai
from modules.rag_pipeline import rag_pipeline
from modules.rewrite_state import rewrite_query_with_context, save_chat_to_file, select_history_chat 
from ingest.database import VietnameseSBERTEmbedding, setup_database
from modules.study_plan import get_elective_configs, parse_knowledge_block, generate_study_plan_range,get_curriculum_metrics
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os

# Import hàm logic lộ trình

# CẤU HÌNH TRANG - PHẢI LÀ LỆNH ĐẦU TIÊN
st.set_page_config(page_title="MIM Chatbot", page_icon="🎓", layout="wide")

load_dotenv()

# --- DÙNG CACHE CHO MODEL VÀ DB (NẶNG) ---
@st.cache_resource
def init_heavy_resources():
    model_sbert = SentenceTransformer("keepitreal/vietnamese-sbert", device="cpu")
    viet_em_fn = VietnameseSBERTEmbedding(model=model_sbert)
    collection = setup_database()
    return collection

# Lấy tài nguyên nặng từ cache
collection = init_heavy_resources()

# KHỞI TẠO CLIENT MỖI LẦN RERUN (NHẸ - AN TOÀN)
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# --- KHỞI TẠO SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_id" not in st.session_state:
    st.session_state.session_id = f"{datetime.now().strftime('%Y%m%d')}_{str(uuid.uuid4())[:8]}"

# --- SIDEBAR NAVIGATION ---
with st.sidebar:
    st.title("🎓 MIM System")

    
    
    mode = st.radio("Chức năng:", ["💬 Chatbot Tư vấn", "🗺️ Lộ trình học tập"])
    
    st.divider()
    if mode == "💬 Chatbot Tư vấn":
        st.title("📚 Lịch sử")
        LOG_DIR = "history_files"
        os.makedirs(LOG_DIR, exist_ok=True)
        # Hàm này sử dụng session_id nên phải đặt sau khi khởi tạo session_id
        select_history_chat(LOG_DIR)

# --- CHẾ ĐỘ 1: CHATBOT TƯ VẤN ---
if mode == "💬 Chatbot Tư vấn":
    st.title("🎓 Chatbot tư vấn đào tạo MIM")

    # Hiển thị lịch sử chat
    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])

    MODEL_NAME = os.getenv("MODEL_NAME")
    if user_input := st.chat_input("Nhập câu hỏi..."):
        st.chat_message("user").write(user_input)
        
        # Rewriting query
        rewritten_query = rewrite_query_with_context(user_input, st.session_state.messages, client, MODEL_NAME)
        st.session_state.messages.append({"role": "user", "content": user_input})

        with st.spinner("Bot đang suy nghĩ..."):
            try:
                response = rag_pipeline(rewritten_query, collection, client)
                
                # Lưu file và kiểm tra nếu có đổi tên file để cập nhật sidebar
                just_renamed = save_chat_to_file(user_input, rewritten_query, response, client, "history_files")
                
                st.chat_message("assistant").write(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
                
                if just_renamed:
                    st.rerun()
            except Exception as e:
                st.error(f"Lỗi: {e}")

# --- CHẾ ĐỘ 2: LỘ TRÌNH HỌC TẬP ---
elif mode == "🗺️ Lộ trình học tập":
    st.title("🗺️ Tư vấn lộ trình học tập")
    st.info("Hệ thống sắp xếp môn học dựa trên quy chế tín chỉ và điều kiện tiên quyết.")

    list_nganh = {
        "Khoa học máy tính": "data_pdf/data_json/quy_che_dao_tao_khmt.json",
        "Toán học": "data_pdf/data_json/quy_che_dao_tao_toanhoc.json",
        "Toán tin": "data_pdf/data_json/quy_che_dao_tao_toantin.json",
        "Khoa học dữ liệu": "data_pdf/data_json/quy_che_dao_tao_khdl.json",
        "Toán học tài năng": "data_pdf/data_json/quy_che_dao_tao_thtn.json"    
    }
    
    selected_nganh = st.selectbox("Chọn ngành học của bạn:", list(list_nganh.keys()))
    path_to_json = list_nganh[selected_nganh]

    # --- Đọc dữ liệu thô để lấy cấu hình tự chọn ---
    try:
        with open(path_to_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_courses_raw = data["phan_3_khung_chuong_trinh_dao_tao"]["2_danh_sach_hoc_phan"]
        
        
    except Exception as e:
        st.error(f"Lỗi đọc file: {e}")
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        cre_range = st.slider("Số tín chỉ mỗi kỳ:", 10, 30, (15, 22))
        min_c, max_c = cre_range
    with col2:
        num_semesters = st.select_slider(
            "Thời gian hoàn thành (số học kỳ):",
            options=list(range(6, 11)),
            value=8
        )

    metrics_data = get_curriculum_metrics(path_to_json)
    total_cre = metrics_data["total"]
    grad_cre = metrics_data["grad_cre"]
    if max_c * num_semesters < total_cre:
        st.error(f"⚠️ Với mức tối đa {max_c} tín/kỳ, bạn không thể hoàn thành {total_cre} tín chỉ trong {num_semesters} kỳ.")
        st.stop()
    is_overload_required = (max_c * (num_semesters - 1)) < (total_cre - grad_cre)

    is_overload_accepted = False
    overload_button_disabled = False
    if is_overload_required:
        st.warning("🔔 Lưu ý: Lộ trình này yêu cầu học thêm môn ở kỳ cuối cùng mới đủ điều kiện ra trường.")
        is_overload_accepted = st.checkbox("Tôi đồng ý học thêm môn ở kỳ cuối")
        overload_button_disabled = not is_overload_accepted
        if not is_overload_accepted:
            st.info("Vui lòng tăng 'Số tín chỉ mỗi kỳ' hoặc 'Số học kỳ' để kỳ cuối nhẹ hơn.")
            st.error("Bạn phải đồng ý học thêm ở kỳ cuối nếu muốn tạo lộ trình này.")

    # Tạo một chỗ chứa trong session_state để lưu plan
    if "current_plan" not in st.session_state:
        st.session_state.current_plan = None

    if st.button("🚀 Tạo lộ trình gợi ý", disabled=overload_button_disabled):
        # Loại bỏ các môn học được học vào kỳ hè cố định khỏi lộ trình
        fixed_summer_courses = {"giáo dục quốc phòng-an ninh", "giáo dục quốc phòng - an ninh", "giáo dục thể chất"}
        filtered_courses = [
            c for c in all_courses_raw
            if c.get("ten_hp_vi", "").strip().lower() not in fixed_summer_courses
        ]
        
        # Chạy thuật toán với danh sách đã lọc
        st.session_state.current_plan = generate_study_plan_range(filtered_courses, min_c, max_c, num_semesters, metrics_data, is_overload_accepted)
        
        # Kiểm tra xem có kỳ nào chưa đạt min_c không
        plan = st.session_state.current_plan
        short_semesters = [i for i in range(1, num_semesters) if sum(int(c['tin_chi']) for c in plan.get(i, [])) < min_c]
        if short_semesters:
            st.warning(f"Lưu ý: Không thể đảm bảo mỗi học kỳ đạt ít nhất {min_c} tín chỉ với dữ liệu hiện tại. Các học kỳ sau đây có thể thiếu tín chỉ: {', '.join(str(i) for i in short_semesters)}.")

        # Hiển thị Tabs
        if plan:
            tabs = st.tabs([f"Học kỳ {i}" for i in range(1, num_semesters + 1)])

            for i, tab in enumerate(tabs):
                with tab:
                    sem_index = i + 1
                    sem_data = st.session_state.current_plan.get(sem_index, [])
                
                    if sem_data:
                        # Chuyển dữ liệu sang DataFrame để hiển thị bảng
                        df = pd.DataFrame(sem_data)
                    
                        # Cấu hình các cột hiển thị
                        display_cols = {
                        "ma_hp": "Mã HP",
                        "ten_hp_vi": "Tên môn học",
                        "tin_chi": "Tín chỉ",
                        "tien_quyet": "Tiên quyết",
                        "khoi_kien_thuc": "Nhóm kiến thức"
                        }
                    
                        # Chỉ lấy những cột có thực tế trong dữ liệu
                        available_cols = [c for c in display_cols.keys() if c in df.columns]
                        df_display = df[available_cols].rename(columns=display_cols)
                    
                        st.table(df_display)
                    
                        # Tính tổng tín chỉ kỳ này
                        sem_total_cre = sum(int(c['tin_chi']) for c in sem_data)
                        st.success(f"**Tổng cộng học kỳ {sem_index}:** {sem_total_cre} tín chỉ")
                    else:
                        if sem_index == num_semesters:
                            st.info("Kỳ cuối thường dành cho Khóa luận tốt nghiệp.")
                        else:
                            st.warning("Không có môn học nào được xếp trong kỳ này.")