import os
import json
import time
import pdfplumber
import re
from google import genai
from google.genai import types
from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")
client = genai.Client(api_key=API_KEY)
prompt_text = """
Bạn là một chuyên gia phân tích dữ liệu giáo dục. Nhiệm vụ của bạn là chuyển đổi file PDF Chương trình đào tạo sang định dạng JSON nguyên khối để xây dựng hệ thống Chatbot.

CẤU TRÚC JSON BẮT BUỘC (STRICT SCHEMA):
Bạn PHẢI tuân thủ đúng phân cấp sau, không được tự ý thêm tầng hoặc đổi tên Key:
{
  "phan_1_thong_tin_chung": {
    "1_mot_so_thong_tin_chuong_trinh_dao_tao": {
      "ten_chuong_trinh_vi": "",
      "ten_chuong_trinh_en": "",
      "ten_nganh_vi": "",
      "ten_nganh_en": "",
      "ma_so": "",
      "trinh_do": "",
      "ngon_ngu_dao_tao": "",
      "thoi_gian_dao_tao": ""
    },
    "2_muc_tieu": {
      "2_1_muc_tieu_chung": "",
      "2_2_muc_tieu_cu_the": ""
    },
    "3_thong_tin_chuyen_sinh": {}
  },
  "phan_2_chuan_dau_ra": {
    "1_chuan_dau_ra_ve_kien_thuc_pk": {},
    "2_chuan_dau_ra_ve_ki_nang_ps": {},
    "3_muc_do_tu_chu_va_trach_nhiem": {},
    "4_vi_tri_viec_lam_sinh_vien": {},
    "5_kha_nang_hoc_tap_nang_cao": {}
  },
  "phan_3_khung_chuong_trinh_dao_tao": {
    "1_tom_tat_yeu_cau": {},
    "2_danh_sach_hoc_phan": [
      {
        "ma_hp": "",
        "ten_hp_vi": "",
        "ten_hp_en": "",
        "tin_chi": "",
        "khoi_kien_thuc": "",
        "ly_thuyet": "",
        "thuc_hanh": "",
        "tu_hoc": "",
        "tien_quyet": ""
      }
    ]
  }
}

YÊU CẦU KỸ THUẬT:
- Phần Văn bản (Unstructured): Trích xuất đầy đủ, không tóm tắt các đoạn văn ở mục I, II và đặc biệt là mục III.1. Đây là dữ liệu dùng cho tìm kiếm ngữ nghĩa (Vector Search).
- Bạn phải sử dụng chính xác 100% các tên trường mục I, II.
- Phần Bảng (Structured): Trích xuất chính xác 100% danh mục môn học ở mục III.2. Đây là dữ liệu dùng cho tra cứu mã học phần và tín chỉ.
- Trong mỗi ô 'Học phần', thường có cả tiếng Việt và tiếng Anh (dòng trên/dòng dưới). Bạn phải trích xuất cả hai và tách tên tiếng Anh ra trường 'ten_hp_en'.
- Với phần khối kiến thức, hãy phân tích tiêu đề của nhóm hoặc ghi chú trong bảng để xác định định mức tín chỉ của khối kiến thức đó và ghi theo kiểu "tên khối kiến thức (định mức)".
- Chỉ trả về mã JSON nguyên khối, không giải thích gì thêm.
"""


PROMPT_QUY_CHE_CHUNG = """
Bạn là một chuyên gia số hóa văn bản hành chính. Nhiệm vụ của bạn là chuyển đổi file PDF quy chế sang định dạng MARKDOWN sạch sẽ.

YÊU CẦU:
1. Giữ nguyên cấu trúc: Các Chương dùng thẻ #, các Điều dùng thẻ ##, các Mục dùng thẻ ###.
2. Nội dung: Trích xuất đầy đủ, chính xác từng chữ, không tóm tắt.
3. Bảng biểu: Nếu có bảng (ví dụ bảng điểm rèn luyện), hãy chuyển sang định dạng Table của Markdown.
4. Nếu trong t liệu có các Bảng Quy Đổi, hãy lệt kê chi tiết các mốc đó.
4. Xử lý lỗi: Tự động nối các dòng bị ngắt quãng do xuống dòng sai chỗ trong PDF.
5. Chỉ trả về Markdown thô, không giải thích gì thêm.
"""
def extract_folder_to_json_md(folder_path, output_folder, is_general=False):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"📁 Đã tạo thư mục lưu trữ: {output_folder}")

    pdf_files = [f for f in os.listdir(folder_path) if f.endswith('.pdf')]
    if not pdf_files:
        print("⚠️ Không tìm thấy file PDF nào!")
        return

    print(f"📂 Tìm thấy {len(pdf_files)} file. Bắt đầu xử lý...")

    for file_name in pdf_files:
        pdf_path = os.path.join(folder_path, file_name)

        # --- BƯỚC 1: Đổi đuôi file dựa theo loại dữ liệu ---
        ext = ".md" if is_general else ".json"
        output_name = file_name.replace(".pdf", ext)
        output_path = os.path.join(output_folder, output_name)

        if os.path.exists(output_path):
            print(f"⏩ Bỏ qua: {file_name}")
            continue

        print(f"🚀 Đang xử lý: {file_name}...")
        current_prompt = PROMPT_QUY_CHE_CHUNG if is_general else prompt_text
        raw_content = None

        try:
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()

            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[
                    types.Part.from_bytes(data=pdf_data, mime_type='application/pdf'),
                    current_prompt
                ]
            )
            if response and response.text:
                raw_content = response.text
            else:
                raise ValueError("Phản hồi Cấp độ 1 rỗng")

        except Exception as e:
            print(f"    ⚠️ Cấp độ 1 thất bại ({e}). Đang thử bằng pdfplumber...")
            raw_text = extract_text_with_pdfplumber(pdf_path)
            if raw_text:
                # Gửi text thô kèm hướng dẫn bổ sung
                fallback_prompt = f"Dưới đây là nội dung văn bản trích xuất từ PDF. Hãy xử lý theo yêu cầu sau:\n{current_prompt}\n\nNỘI DUNG:\n{raw_text}"

                response = client.models.generate_content(model=MODEL_NAME, contents=fallback_prompt)
                raw_content = response.text if response else None
            else:
                raw_content = None

            # --- BƯỚC 3: Lưu dữ liệu (Markdown lưu thô, JSON thì parse) ---
        if raw_content:
            try:
                clean_content = clean_gemini_response(raw_content)
                # Làm sạch các ký hiệu ``` nếu AI trả về
                if is_general:
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(clean_content)
                else:
                    data = json.loads(clean_content)
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                print(f"✅ Đã lưu: {output_path}")
            except Exception as e_format:
                print(f"❌ Lỗi định dạng/JSON cho {file_name}: {e_format}")
        else:
            print(f"❌ Lỗi tổng quát cho {file_name}")

        time.sleep(10 if is_general else 2)

def extract_text_with_pdfplumber(pdf_path):
    """Hàm dự phòng: Trích xuất text thô khi Gemini không đọc được file bytes trực tiếp"""
    full_text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
        return full_text
    except Exception as e:
        print(f"    ⚠️ Lỗi pdfplumber: {e}")
        return None
def clean_gemini_response(text):
    """Làm sạch các tag ```json hoặc ```markdown từ phản hồi của AI"""
    clean = re.sub(r"```(json|markdown)?", "", text)
    clean = clean.replace("```", "").strip()
    return clean