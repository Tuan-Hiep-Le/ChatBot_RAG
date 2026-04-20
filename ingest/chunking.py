
import os
import json
import re

def chunk_from_json(json_data):
    chunks = []
    metadata = []
    intro_part = json_data.get('phan_1_thong_tin_chung', {})
    program_info = intro_part.get('1_mot_so_thong_tin_chuong_trinh_dao_tao', {})

    # Ưu tiên lấy ngành_dao_tao_vi, nếu không có thì lấy tên chương trình
    ten_nganh = program_info.get('ten_nganh_vi') or \
                program_info.get('ten_chuong_trinh_vi') or \
                "Ngành chưa xác định"

    # 1. Xử lý danh mục môn học

    phan_3 = json_data.get('phan_3_khung_chuong_trinh_dao_tao', {})

    for key, value in phan_3.items():
      if key != '2_danh_sach_hoc_phan' and value:
        if isinstance(value, dict):
            # Biến dict thành chuỗi văn bản thuần túy
            summary_str = ", ".join([f"{k.replace('_', ' ')} là {v}" for k, v in value.items()])
            info_text = f"Tổng quan khung chương trình ngành {ten_nganh}: {summary_str}"
        else:
            info_text = f"Ngành {ten_nganh} - {key}: {value}"

        chunks.append(info_text)
        metadata.append({"source": ten_nganh, "type": "tong_quan_tin_chi"})

    mon_hoc_list = phan_3.get('2_danh_sach_hoc_phan', [])
    for mon in mon_hoc_list:
        # --- Trích xuất dữ liệu an toàn ---
        ma_hp = mon.get('ma_hp')
        ten_vi = mon.get('ten_hp_vi', 'N/A')
        ten_en = mon.get('ten_hp_en', 'N/A')

        # Xử lý số tín chỉ và giờ học (ưu tiên lấy số, mặc định là 0)
        so_tc = mon.get('tin_chi') or 0
        lt = mon.get('ly_thuyet') or 0
        th = mon.get('thuc_hanh') or 0
        tu_hoc = mon.get('tu_hoc') or 0

        khoi_kt = mon.get('khoi_kien_thuc', 'Chưa phân loại')
        tien_quyet = mon.get('tien_quyet', 'Không có')

        # --- Tạo chuỗi văn bản (Tối ưu cho Vector Search) ---
        mon_text = (
            f"Chương trình đào tạo ngành: {ten_nganh}. "
            f"Học phần: {ten_vi} ({ten_en}). "
            f"Mã số: {ma_hp}. "
            f"Số tín chỉ: {so_tc}. "
            f"Chi tiết thời lượng: {lt} giờ lý thuyết, {th} giờ thực hành, {tu_hoc} giờ tự học. "
            f"Thuộc khối kiến thức: {khoi_kt}. "
            f"Điều kiện tiên quyết: {tien_quyet}."
        ).strip()

        chunks.append(mon_text)

        # --- Metadata chi tiết (Tối ưu cho Filtering) ---
        metadata.append({
            "source": ten_nganh,
            "type": "mon_hoc",
            "ma_hp": ma_hp,
            "ten_hp_vn": ten_vi,
            "so_tin_chi": int(so_tc) if str(so_tc).isdigit() else 0,
            "ly_thuyet": int(lt) if str(lt).isdigit() else 0,
            "thuc_hanh": int(th) if str(th).isdigit() else 0,
            "tu_hoc": int(tu_hoc) if str(tu_hoc).isdigit() else 0,
            "khoi_kien_thuc": khoi_kt
        })

    # 2. Xử lý phần thông tin chung (Quy định, Mục tiêu)
    sections_to_process = [
        ("Mục tiêu", intro_part.get('2_muc_tieu', {})),
        ("Tuyển sinh", intro_part.get('3_thong_tin_chuyen_sinh', {})),
        ("Chuẩn đầu ra Kiến thức", json_data.get('phan_2_chuan_dau_ra', {}).get('1_chuan_dau_ra_ve_kien_thuc_pk', {})),
        ("Chuẩn đầu ra Kỹ năng", json_data.get('phan_2_chuan_dau_ra', {}).get('2_chuan_dau_ra_ve_ki_nang_ps', {})),
        ("Trách nhiệm", json_data.get('phan_2_chuan_dau_ra', {}).get('3_muc_do_tu_chu_va_trach_nhiem', {})),
        ("Cơ hội việc làm", json_data.get('phan_2_chuan_dau_ra', {}).get('4_vi_tri_viec_lam_sinh_vien', {}))
    ]

    for label, content_dict in sections_to_process:
        if isinstance(content_dict, dict):
            for key, text in content_dict.items():
                if text and len(str(text)) > 20:
                    # Nếu text là list (như vị trí việc làm), nối lại thành chuỗi
                    if isinstance(text, list): text = ". ".join(text)

                    full_text = f"Ngành {ten_nganh} - {label} ({key}): {text}"
                    chunks.append(full_text)
                    metadata.append({"source": ten_nganh, "type": "thong_tin_chung", "sub_type": label})

    return chunks, metadata


def process_all_files(folder_path):
    if not os.path.exists(folder_path):
        print(f"❌ Lỗi: Thư mục '{folder_path}' không tồn tại!")
        return [], []
    all_chunks = []
    all_metadatas = []

    # Lấy danh sách tất cả file .json trong thư mục
    json_files = [f for f in os.listdir(folder_path) if f.endswith('.json')]

    for file_name in json_files:
        file_path = os.path.join(folder_path, file_name)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                chunks, metadatas = chunk_from_json(json_data)

                for meta in metadatas:
                    meta["file_name"] = file_name
                     # Đây là chìa khóa để Router nhận diện "Tài liệu ngành"
                    meta["doc_group"] = "quy_che_chuyen_nganh"
                    # Dùng tên file (không có .json) làm category như bên Quy chế
                    meta["category"] = file_name.replace(".json", "")
                    # Gọi hàm chunking của bạn cho từng file
                    # BỔ SUNG: Gắn thêm tên file gốc vào metadata để truy vết

                # Gộp kết quả vào danh sách tổng
                all_chunks.extend(chunks)
                all_metadatas.extend(metadatas)

                print(f"--- Đã xử lý xong: {file_name} ({len(chunks)} chunks)")
        except Exception as e:
            print(f"Lỗi khi đọc file {file_name}: {e}")

    return all_chunks, all_metadatas

def universal_markdown_chunker(md_content, file_name, category):
    # Cắt dựa trên tiêu đề cấp 2 (##) - áp dụng cho mọi loại văn bản
    # Regex này tìm dòng bắt đầu bằng ## và cắt tại đó
    sections = re.split(r'\n(?=## )', md_content)

    chunks = []
    metadatas = []

    for section in sections:
        clean_section = section.strip()
        if not clean_section:
            continue

        # Tiêm ngữ cảnh để AI luôn biết mảnh này thuộc tài liệu nào

        # Trích xuất tiêu đề của chunk để làm metadata (giúp search chính xác hơn)
        # Lấy dòng đầu tiên làm tiêu đề mục
        header_line = clean_section.splitlines()[0] if clean_section.splitlines() else "Thông tin chung"

            # --- ÁP DỤNG CHIẾN THUẬT LÀM GIÀU ---
        if "|" in clean_section:
                # Cách làm giàu cho bảng biểu (Cực mạnh)
            enriched_text = f"TÀI LIỆU: {file_name}\n"
            enriched_text += f"MỤC: {header_line}\n"
            enriched_text += f"NỘI DUNG TRA CỨU: {clean_section}"
        else:
                # Cách làm giàu cho văn bản thường
            contextual_text = f"Tài liệu: {category.upper()}\n{clean_section}"
        chunks.append(contextual_text)
        metadatas.append({
            "source": file_name,
            "category": category,
            "section_title": header_line,
            "type": "quy_che_chung"
        })

    return chunks, metadatas



def process_general_documents(folder_path):
    if not os.path.exists(folder_path):
        print(f"❌ Lỗi: Thư mục '{folder_path}' không tồn tại!")
        return [], []

    all_chunks = []
    all_metadatas = []

    # 1. Chỉ lấy file .md
    md_files = [f for f in os.listdir(folder_path) if f.endswith('.md')]

    for file_name in md_files:
        file_path = os.path.join(folder_path, file_name)
        category = file_name.replace(".md", "")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                md_content = f.read()

            # 2. Sử dụng logic cắt theo Header (##)
            # Cách này áp dụng tốt cho cả file có "Điều" và file "Ngoại ngữ" chỉ có tiêu đề mục
            sections = re.split(r'\n(?=## |### |#### )', md_content)

            for section in sections:
                clean_section = section.strip()
                if not clean_section:
                    continue

                # 3. Tiêm ngữ cảnh (Context Injection)
                # Giúp AI biết mảnh này thuộc quy chế nào khi ở trong Vector DB
                contextual_text = f"Tài liệu: {category.upper()}\n{clean_section}"

                all_chunks.append(contextual_text)

                # 4. Lưu Metadata để lọc (Filtering)
                all_metadatas.append({
                    "source": file_name,
                    "type": "quy_che_chung",
                    "category": category
                })

            print(f"--- Đã xử lý xong: {file_name} ({len(sections)} chunks)")

        except Exception as e:
            print(f"Lỗi khi đọc file {file_name}: {e}")

    return all_chunks, all_metadatas

