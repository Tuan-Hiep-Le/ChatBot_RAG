import streamlit as st
from google import genai
from google.genai import types
from dotenv import load_dotenv
import json
from datetime import datetime
import uuid
import re
import os
from modules.router import get_retrieval_config_hybrid
load_dotenv()

# --- BƯỚC 1: KHỞI TẠO SESSION STATE ---


# Hàm để Gemini viết lại câu hỏi dựa trên ngữ cảnh
def rewrite_query_with_context(user_query, chat_history, client,model_name):
    config = get_retrieval_config_hybrid(user_query, client)
    if config["filter"] == None:
        return user_query

    if not chat_history:
        return user_query

    # Chỉ lấy 3 cặp hội thoại gần nhất để tránh quá tải token
    recent_history = chat_history[-3:]
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in recent_history])

    prompt = f"""
    Dựa trên lịch sử trò chuyện và câu hỏi mới của sinh viên, hãy viết lại câu hỏi đó thành một câu hoàn chỉnh, 
    chứa đầy đủ tên ngành học hoặc khối kiến thức nếu chúng đã được nhắc đến ở trên.
    
    Lịch sử:
    {history_text}
    
    Câu hỏi mới: "{user_query}"
    
    Chỉ trả về câu hỏi đã viết lại, không giải thích gì thêm.
    """
    
    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text.strip()



def save_chat_to_file(user_query, rewritten_query,bot_response,client,LOG_DIR):

    config = get_retrieval_config_hybrid(user_query, client) # Lấy config để kiểm tra filter
    print("Retrieval Config: ", config.get("filter"))
    if "has_real_title" not in st.session_state:    #Kiểm tra nếu chưa có trạng thái này thì khởi tạo, tránh lỗi khi lần đầu tiên chạy
        st.session_state.has_real_title = False
    print("Has Real Title 1: ", st.session_state.has_real_title)

    if not st.session_state.has_real_title and config.get("filter") is not None: #Nếu chưa có tên xịn và filter không phải None thì tạo tên xịn
        clean_title = re.sub(r'[^\w\s]', '', user_query)[:30].strip().replace(" ", "_") # Làm sạch câu hỏi để tạo tên file, giữ tối đa 30 ký tự
        print("Clean Title: ", clean_title)
        st.session_state.chat_title = f"{datetime.now().strftime('%m%d')}_{clean_title}" # Tạo tên file dựa trên ngày và câu hỏi đã làm sạch
        st.session_state.has_real_title = True # Đánh dấu: Đã có tên xịn, không đổi nữa
    
    print("Has Real Title 2: ", st.session_state.has_real_title)

    if "chat_title" not in st.session_state: # Nếu chưa có tên nào cả (ví dụ lần đầu tiên chạy và filter là None), thì tạo tên mặc định
        st.session_state.chat_title = f"{datetime.now().strftime('%m%d')}_Cuoc_tro_chuyen"

    file_path = os.path.join(LOG_DIR, f"chat_{st.session_state.session_id}.json")

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "chat_title": st.session_state.chat_title,  
        "rewritten_query": rewritten_query,
        "user": user_query,
        "bot": bot_response,
        "filter": config.get("filter")  # Lưu thêm thông tin filter để dễ dàng phân loại sau này
    }

    history = []
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []

    history.append(log_entry)
    # Lưu nối tiếp (append) vào file chat_logs.json
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)
    
     
def select_history_chat(LOG_DIR):

    if st.sidebar.button("➕ Tạo phiên chat mới", use_container_width=True):
        st.session_state.messages = []
        if "chat_title" in st.session_state:
            del st.session_state.chat_title
        if "has_real_title" in st.session_state:
            st.session_state.has_real_title = False
        # Tạo ID mới hoàn toàn để không ghi đè vào file cũ
        st.session_state.session_id = f"{datetime.now().strftime('%Y%m%d')}_{str(uuid.uuid4())[:8]}"
        st.rerun()

    st.sidebar.divider() # Vạch ngăn cách

    if not os.path.exists(LOG_DIR):
        st.warning("Chưa có lịch sử trò chuyện nào.")
        return None

    files = [f for f in os.listdir(LOG_DIR) if f.endswith(".json")]
    
    if not files:
        st.warning("Chưa có lịch sử trò chuyện nào.")
        return None
    files.sort(reverse=True)  
    current_filename = f"chat_{st.session_state.session_id}.json"
    try:
        current_index = files.index(current_filename)
    except ValueError:
        current_index = 0
    def get_file_label(filename):
        try:
            with open(os.path.join(LOG_DIR, filename), "r", encoding="utf-8") as f:
                data = json.load(f)
                # Lấy chat_title từ tin nhắn đầu tiên (entry 0)
                if data and isinstance(data, list):
                    for entry in data:
                        filter_value = entry.get("filter")
                        if filter_value and filter_value != "None" and filter_value != "null":
                            return entry.get("user")
                    # Nếu có chat_title thì hiện, không thì hiện ID cũ làm dự phòng
                    return data[0].get("chat_title", "Cuộc trò chuyện")
        except:
            pass
        return filename.replace("chat_", "").replace(".json", "")

    if files:
        selected_file = st.sidebar.selectbox(
            "Chọn phiên chat cũ:",
            options=files,
            index=current_index if current_index < len(files) else 0,
            format_func=get_file_label
        )

        if st.sidebar.button("Nạp lại phiên này"):
            file_path = os.path.join(LOG_DIR, selected_file)
            with open(file_path, "r", encoding="utf-8") as f:
                history_data = json.load(f)
            
            if history_data:
                # Lấy lại tiêu đề đã lưu trong file
                st.session_state.chat_title = history_data[0].get("chat_title")
        
                # Kiểm tra xem trong file đã từng có filter nào chưa
                has_any_filter = any(entry.get("filter") for entry in history_data)
                st.session_state.has_real_title = has_any_filter

            # Chuyển đổi dữ liệu nạp vào session_state
            new_messages = []
            for entry in history_data:
                new_messages.append({"role": "user", "content": entry["user"]})
                new_messages.append({"role": "assistant", "content": entry["bot"]})
        
            st.session_state.messages = new_messages
            # Cập nhật ID phiên hiện tại thành ID của file vừa nạp để chat tiếp vào đó
            st.session_state.session_id = selected_file.replace("chat_", "").replace(".json", "")
            st.rerun()
    else:
        st.warning("Chưa có lịch sử trò chuyện nào.")