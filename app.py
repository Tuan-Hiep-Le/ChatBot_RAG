import streamlit as st
import chromadb
import uuid
import json
import io
import pandas as pd
import re
from datetime import datetime
from google import genai
try:
    from google.genai import errors as genai_errors
except Exception:
    genai_errors = None
from modules.rag_pipeline import rag_pipeline
from modules.rewrite_state import rewrite_query_with_context, save_chat_to_file, select_history_chat 
from ingest.database import VietnameseSBERTEmbedding, setup_database
from modules.study_plan import get_elective_configs, parse_knowledge_block, generate_study_plan_range,get_curriculum_metrics, mentor_pipeline
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os

# --- HỖ TRỢ TRỢ LÝ MENTOR ---
import streamlit as st

def is_quota_error(exc):
    if exc is None:
        return False
    if genai_errors is not None and isinstance(exc, getattr(genai_errors, "ClientError", Exception)):
        return getattr(exc, "status", "").upper() == "RESOURCE_EXHAUSTED"
    text = str(exc).lower()
    return any(token in text for token in ["quota", "resource_exhausted", "429", "free_tier_requests"])

def render_mentor_panel(selected_nganh, num_semesters, min_c, max_c, total_cre, grad_cre, current_plan, all_courses_raw, collection, client):
    st.subheader("🧑‍🏫 Trợ lý Mentor")
    st.markdown("Trợ lý Mentor giúp bạn hiểu lộ trình, dự báo tiến độ, tư vấn lựa chọn và tương tác nhanh.")

    # --- [GIỮ NGUYÊN] Phần 1: Phân tích và dự báo lộ trình tĩnh của bạn ---
    avg_needed = total_cre / num_semesters if num_semesters else 0
    st.markdown(f"- Ngành: **{selected_nganh}**")
    st.markdown(f"- Tổng tín chỉ cần hoàn thành: **{total_cre}** tín")
    st.markdown(f"- Trung bình cần đạt: **{avg_needed:.1f}** tín/kỳ")
    st.markdown(f"- Mốc tín chỉ tốt nghiệp dự kiến: **{grad_cre}** tín")
    
    if max_c * num_semesters < total_cre:
        st.error("⚠️ Với mức tín chỉ tối đa hiện tại, bạn không thể hoàn thành chương trình trong số kỳ đã chọn.")
    elif min_c * num_semesters > total_cre:
        st.success("🎉 Bạn có thể hoàn thành chương trình sớm hơn nếu phân bổ tốt.")
    elif avg_needed > max_c:
        st.warning("⚠️ Kế hoạch hiện tại có thể yêu cầu quá tải ở một số kỳ. Xem kỹ đề xuất bên dưới.")
    else:
        st.info("Kế hoạch này có thể phù hợp nếu bạn duy trì đều đặn mỗi kỳ.")

    if current_plan:
        sem_totals = [sum(int(c["tin_chi"]) for c in current_plan.get(i, [])) for i in range(1, num_semesters + 1)]
        overloaded = [i for i, tot in enumerate(sem_totals, start=1) if tot > max_c]
        underloaded = [i for i, tot in enumerate(sem_totals, start=1) if tot < min_c and i != num_semesters]

        if overloaded:
            st.warning(f"🔺 Các kỳ có tín chỉ vượt ngưỡng tối đa: {', '.join(map(str, overloaded))}.")
        if underloaded:
            st.warning(f"🔻 Các kỳ có tín chỉ dưới ngưỡng tối thiểu: {', '.join(map(str, underloaded))}.")
        if not overloaded and not underloaded:
            st.success("📈 Lộ trình hiện tại ổn định với điều kiện tối thiểu/tối đa.")

    st.divider()
    st.markdown("**Ghi chú Mentor:**")
    if avg_needed > max_c:
        st.markdown("- Bạn nên xem xét tăng số kỳ hoặc tăng giới hạn tín chỉ mỗi kỳ nếu có thể.")
    elif avg_needed < min_c:
        st.markdown("- Bạn có thể điều chỉnh để nhẹ hơn hoặc hoàn thành sớm hơn.")
    else:
        st.markdown("- Giữ nhịp học ổn định, ưu tiên các môn tiên quyết trước.")
    if max_c * (num_semesters - 1) < (total_cre - grad_cre):
        st.markdown("- Khóa luận tốt nghiệp sẽ chiếm kỳ cuối, nên nên đảm bảo các môn tiên quyết hoàn thành trước đó.")
    
    st.divider()

    # --- [NÂNG CẤP CHUYÊN NGHIỆP] Phần 2: Khung Chatbot mượt mà ---
    st.subheader("💬 Trao đổi với Mentor")

    # 1. Khởi tạo lịch sử chat chuẩn Streamlit nếu chưa có
    if "mentor_chat_history" not in st.session_state:
        st.session_state.mentor_chat_history = [
            {
                "role": "assistant", 
                "content": f"Chào em! Thầy là Mentor tư vấn học tập ngành **{selected_nganh}**. Thầy đã phân tích lộ trình của em ở trên. Em có thắc mắc gì về quy chế, độ nặng các môn hay muốn thầy chèn/bớt môn học nào không?"
            }
        ]

    # Nút bấm nhỏ để xóa lịch sử trò chuyện khi sinh viên muốn hỏi chủ đề mới
    col_space, col_clear = st.columns([6, 1])
    with col_clear:
        if st.button("🗑️ Xóa chat", help="Xóa lịch sử để chat lại từ đầu"):
            st.session_state.mentor_chat_history = [
                {"role": "assistant", "content": f"Chào em! Thầy là Mentor tư vấn học tập ngành **{selected_nganh}**. Thầy có thể giúp gì cho em?"}
            ]
            st.rerun()

    # Tạo một container cố định để chứa các tin nhắn, tránh việc giao diện nhảy lung tung
    chat_container = st.container()

    # 2. Hiển thị lại toàn bộ lịch sử trò chuyện dạng bong bóng chat chuyên nghiệp
    with chat_container:
        for message in st.session_state.mentor_chat_history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    # 3. Sử dụng st.chat_input thay thế cho ô nhập text tĩnh cũ
    if user_question := st.chat_input("Nhập câu hỏi của bạn cho Mentor (Ví dụ: Thêm môn Giải tích 2, Kỳ 3 học nặng không thầy?)..."):
        
        # Hiển thị ngay tin nhắn của cơ điện sinh viên lên màn hình UI
        with chat_container:
            with st.chat_message("user"):
                st.markdown(user_question)
        
        # Lưu câu hỏi của sinh viên vào session state
        st.session_state.mentor_chat_history.append({"role": "user", "content": user_question})

        # Tiến hành gọi xử lý qua AI pipeline của bạn
        with chat_container:
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                message_placeholder.markdown("*Mentor đang phân tích câu hỏi và đối chiếu quy chế...*")
                
                try:
                    # Gọi pipeline xử lý chính xác các tham số của bạn
                    mentor_result = mentor_pipeline(
                        user_question,
                        selected_nganh,
                        num_semesters,
                        min_c,
                        max_c,
                        total_cre,
                        grad_cre,
                        st.session_state.current_plan,  # Lấy trực tiếp từ session_state để đồng bộ thay đổi
                        collection,
                        client,
                        all_courses_raw,
                    )
                    
                    # Bóc tách kết quả trả về từ pipeline của bạn
                    if isinstance(mentor_result, dict):
                        answer = mentor_result.get("answer", "Thầy gặp chút trục trặc dữ liệu, em hỏi lại nhé.")
                        updated_plan = mentor_result.get("updated_plan")
                        
                        # Điểm mấu chốt: Cập nhật lộ trình lên bảng tổng phía trên của giao diện chính
                        if updated_plan is not None:
                            st.session_state.current_plan = updated_plan
                            st.toast("🔄 Lộ trình học tập phía trên đã tự động cập nhật thành công!", icon="✅")
                    else:
                        answer = mentor_result

                    # Render câu trả lời chuẩn của AI lên UI
                    message_placeholder.markdown(answer)
                    
                    # Lưu câu trả lời của Mentor vào lịch sử chat
                    st.session_state.mentor_chat_history.append({"role": "assistant", "content": answer})
                    
                    # Nếu có sự thay đổi lộ trình (updated_plan), ép Streamlit reload để vẽ lại bảng học kỳ ngay tức khắc
                    if isinstance(mentor_result, dict) and mentor_result.get("updated_plan") is not None:
                        st.rerun()
                        
                except Exception as e:
                    if is_quota_error(e):
                        message_placeholder.markdown("❌ **Hết quota:** Hiện tại hệ thống đã vượt hạn mức API. Vui lòng thử lại sau vài phút.")
                    else:
                        message_placeholder.markdown(f"❌ **Có lỗi kết nối đến Mentor:** `{str(e)}`")

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

if "mentor_history" not in st.session_state:
    st.session_state.mentor_history = []

# --- SIDEBAR NAVIGATION ---
with st.sidebar:
    st.title("🎓 MIM System")

    
    
    mode = st.radio("Chức năng:", ["💬 Chatbot Tư vấn", "🗺️ Lộ trình học tập", "🎓 Dự báo tốt nghiệp"])
    
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
        proceed_with_query = True
        try:
            rewritten_query = rewrite_query_with_context(user_input, st.session_state.messages, client, MODEL_NAME)
        except Exception as e:
            if is_quota_error(e):
                st.error("Hết quota: hệ thống đã vượt hạn mức yêu cầu Gemini. Vui lòng thử lại sau vài phút.")
                proceed_with_query = False
            else:
                st.warning("Không thể viết lại câu hỏi, dùng câu hỏi gốc để tiếp tục.")
                rewritten_query = user_input

        if not proceed_with_query:
            rewritten_query = None
        else:
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
                    if is_quota_error(e):
                        st.error("Hết quota: hệ thống đã vượt hạn mức yêu cầu Gemini. Vui lòng thử lại sau vài phút.")
                    else:
                        st.error(f"Lỗi: {e}")

# --- CHẾ ĐỘ 2: DỰ BÁO TỐT NGHIỆP ---
elif mode == "🎓 Dự báo tốt nghiệp":
    st.title("🎓 Dự báo tốt nghiệp")
    st.info("Kiểm tra tiến độ học tập và dự báo khả năng tốt nghiệp dựa trên môn đã học.")

    list_nganh = {
        "Khoa học máy tính": "data_pdf/data_json/quy_che_dao_tao_khmt.json",
        "Toán học": "data_pdf/data_json/quy_che_dao_tao_toanhoc.json",
        "Toán tin": "data_pdf/data_json/quy_che_dao_tao_toantin.json",
        "Khoa học dữ liệu": "data_pdf/data_json/quy_che_dao_tao_khdl.json",
        "Toán học tài năng": "data_pdf/data_json/quy_che_dao_tao_thtn.json"    
    }
    
    selected_nganh = st.selectbox("Chọn ngành học của bạn:", list(list_nganh.keys()))
    path_to_json = list_nganh[selected_nganh]

    # Đọc dữ liệu
    try:
        with open(path_to_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_courses_raw = data["phan_3_khung_chuong_trinh_dao_tao"]["2_danh_sach_hoc_phan"]
    except Exception as e:
        st.error(f"Lỗi đọc file: {e}")
        st.stop()

    # Lấy elective configs
    elective_configs = get_elective_configs(all_courses_raw)
    metrics_data = get_curriculum_metrics(path_to_json)
    total_cre = metrics_data["total"]
    grad_cre = metrics_data["grad_cre"]

    # Input danh sách môn đã học
    st.subheader("📝 Nhập danh sách môn đã học")
    completed_input = st.text_area(
        "Nhập danh sách mã học phần đã học (cách nhau bởi dấu phẩy, dòng mới, hoặc khoảng trắng):",
        placeholder="Ví dụ: MAT2502, PHI1006, MAT2034"
    )

    if st.button("🔍 Kiểm tra tiến độ"):
        if not completed_input.strip():
            st.error("Vui lòng nhập danh sách môn đã học.")
            st.stop()

        # Parse input
        completed_codes = re.split(r'[,\s\n]+', completed_input.strip())
        completed_codes = [code.strip().upper() for code in completed_codes if code.strip()]

        # Tìm môn đã học
        completed_courses = []
        for code in completed_codes:
            course = next((c for c in all_courses_raw if c.get("ma_hp", "").upper() == code), None)
            if course:
                completed_courses.append(course)
            else:
                st.warning(f"Không tìm thấy môn học với mã: {code}")

        if not completed_courses:
            st.error("Không có môn học hợp lệ nào được tìm thấy.")
            st.stop()

        # Tính tín chỉ đã học
        total_completed_cre = sum(int(c['tin_chi']) for c in completed_courses)

        # Tính cho từng block
        block_progress = []
        for config in elective_configs:
            block_completed = [c for c in completed_courses if c['khoi_kien_thuc'] == config['raw_name']]
            completed_cre = sum(int(c['tin_chi']) for c in block_completed)
            required = config['required_credits']
            declared = config.get('declared_total') or config.get('total_available')
            status = "✅ Đủ" if completed_cre >= required else f"❌ Thiếu {required - completed_cre} tín chỉ"
            block_progress.append({
                "Khối kiến thức": config['block_name'],
                "Đã học": f"{completed_cre} tín",
                "Cần": f"{required} tín trên {declared} tín",
                "Trạng thái": status
            })

        # Hiển thị kết quả
        st.success(f"**Tổng tín chỉ đã học:** {total_completed_cre}/{total_cre}")
        if total_completed_cre >= grad_cre:
            st.success("🎉 Bạn đã đủ điều kiện tốt nghiệp!")
        else:
            st.warning(f"⚠️ Bạn còn thiếu {grad_cre - total_completed_cre} tín chỉ để đủ điều kiện tốt nghiệp (yêu cầu tối thiểu {grad_cre} tín chỉ).")

        # Bảng tiến độ từng khối
        st.subheader("📊 Tiến độ từng khối kiến thức")
        df_progress = pd.DataFrame(block_progress)
        st.table(df_progress)

        # Môn còn thiếu (tùy chọn)
        st.subheader("📚 Môn học còn thiếu (gợi ý)")
        missing_courses = []
        for config in elective_configs:
            block_completed_codes = {c['ma_hp'] for c in completed_courses if c['khoi_kien_thuc'] == config['raw_name']}
            required_cre = config['required_credits']
            completed_cre = sum(int(c['tin_chi']) for c in completed_courses if c['khoi_kien_thuc'] == config['raw_name'])
            if completed_cre < required_cre:
                # Gợi ý môn còn thiếu (đơn giản: lấy môn đầu tiên chưa học)
                for c in config['courses']:
                    if c['ma_hp'] not in block_completed_codes:
                        missing_courses.append({
                            "Khối": config['block_name'],
                            "Môn gợi ý": f"{c['ten_hp_vi']} ({c['ma_hp']})",
                            "Tín chỉ": c['tin_chi']
                        })
                        break  # Chỉ gợi ý 1 môn/block

        if missing_courses:
            df_missing = pd.DataFrame(missing_courses)
            st.table(df_missing)
        else:
            st.info("Bạn đã hoàn thành tất cả các khối kiến thức!")

# --- CHẾ ĐỘ 3: LỘ TRÌNH HỌC TẬP ---
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

    metrics_data = get_curriculum_metrics(path_to_json)
    total_cre = metrics_data["total"]
    grad_cre = metrics_data["grad_cre"]

    if "current_plan" not in st.session_state:
        st.session_state.current_plan = None

    st.subheader("🚀 Tạo lộ trình học tập")
    
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
    conditional_cre = 3  # 3 tín chỉ môn điều kiện bắt buộc (không tính vào 129 tín tích lũy)
    actual_total_to_arrange = total_cre + conditional_cre  # Tổng số tín thực tế thuật toán phải xếp lịch (132 tín)

    if max_c * num_semesters < (actual_total_to_arrange):
        st.error(f"⚠️ Với mức tối đa {max_c} tín/kỳ, bạn không thể hoàn thành {actual_total_to_arrange} tín chỉ trong {num_semesters} kỳ.")
        st.stop()
    is_overload_required = (max_c * (num_semesters - 1)) < (actual_total_to_arrange - grad_cre)

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

    # Hiển thị lộ trình nếu có
    if st.session_state.current_plan:
        plan = st.session_state.current_plan
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

        # Chức năng export lộ trình
        st.divider()
        st.subheader("📥 Xuất lộ trình học tập")

        col_export1, col_export2 = st.columns(2)

        with col_export1:
            # Export ra JSON
            export_data = {}
            for sem in range(1, num_semesters + 1):
                sem_data = plan.get(sem, [])
                export_data[f"Học kỳ {sem}"] = [
                    {
                        "Mã HP": c.get("ma_hp", ""),
                        "Tên môn học": c.get("ten_hp_vi", ""),
                        "Tín chỉ": c.get("tin_chi", ""),
                        "Tiên quyết": c.get("tien_quyet", ""),
                        "Nhóm kiến thức": c.get("khoi_kien_thuc", "")
                    } for c in sem_data
                ]
            json_str = json.dumps(export_data, ensure_ascii=False, indent=4)
            st.download_button(
                label="📄 Tải xuống JSON",
                data=json_str,
                file_name="lo_trinh_hoc_tap.json",
                mime="application/json",
                key="download_json"
            )

        with col_export2:
            # Export ra CSV
            all_rows = []
            for sem in range(1, num_semesters + 1):
                sem_data = plan.get(sem, [])
                for c in sem_data:
                    all_rows.append({
                        "Học kỳ": sem,
                        "Mã HP": c.get("ma_hp", ""),
                        "Tên môn học": c.get("ten_hp_vi", ""),
                        "Tín chỉ": c.get("tin_chi", ""),
                        "Tiên quyết": c.get("tien_quyet", ""),
                        "Nhóm kiến thức": c.get("khoi_kien_thuc", "")
                    })
            df_export = pd.DataFrame(all_rows)
            buffer = io.BytesIO()
            df_export.to_csv(buffer, index=False, encoding='utf-8-sig')
            csv = buffer.getvalue()
            st.download_button(
                label="📊 Tải xuống CSV",
                data=csv,
                file_name="lo_trinh_hoc_tap.csv",
                mime="text/csv",
                key="download_csv"
            )

    st.divider()
    render_mentor_panel(selected_nganh, num_semesters, min_c, max_c, total_cre, grad_cre, st.session_state.current_plan, all_courses_raw, collection, client)