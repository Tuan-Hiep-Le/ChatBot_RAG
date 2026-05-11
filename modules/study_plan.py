import streamlit as st
import json
import pandas as pd
import re

#Hàm bóc tách tín chỉ định mức của từng khối kiến thức 
def parse_knowledge_block(block_name):
    match = re.search(r"\((\d+)(?:/(\d+))?\)", block_name)
    if match:
        required = int(match.group(1)) 
        available = int(match.group(2)) if match.group(2) else required
        clean_name = re.sub(r"\s*\(.*?\)", "", block_name).strip()
        return clean_name, required, available
    return block_name, 0, 0

#Trích xuất các khối kiến thức

def get_elective_configs(all_courses):
    """
    Sử dụng parse_knowledge_block để trích xuất danh sách cấu hình tự chọn.
    """
    elective_configs = []
    
    # 1. Lấy danh sách các tên khối kiến thức độc nhất
    unique_blocks = sorted(list(set(c['khoi_kien_thuc'] for c in all_courses)))
    
    for block_name in unique_blocks:
        # SỬ DỤNG HÀM CỦA BẠN TẠI ĐÂY
        clean_name, required, available = parse_knowledge_block(block_name)
        
        # Nếu hàm parse_knowledge_block tìm thấy định dạng (X/Y)
        # (Dựa trên hàm của bạn: required > 0 nếu có số X/Y)
        if required > 0:
            # Lọc danh sách môn thuộc khối này
            courses_in_block = [c for c in all_courses if c['khoi_kien_thuc'] == block_name]
            
            # Tính toán số tín thực tế đang có trong danh sách môn
            actual_total = sum(int(c['tin_chi']) for c in courses_in_block)
            
            elective_configs.append({
                "block_name": clean_name,        # Tên sạch để hiển thị UI
                "raw_name": block_name,         # Tên gốc để map dữ liệu
                "required_credits": required,   # Số tín chỉ sinh viên cần chọn (ví dụ 12)
                "total_available": actual_total, # Tổng số tín chỉ có sẵn trong file (ví dụ 39)
                "courses": courses_in_block
            })
            
    return elective_configs


# 1. Hàm logic phân bổ lộ trình (đầu vào là JSON)
def generate_study_plan_range(all_courses, min_cre, max_cre, num_semesters,metrics,is_overload_accepted = False):
    total_cre = metrics["total"]
    grad_cre = metrics["grad_cre"]

    # XÁC ĐỊNH TARGET CREDITS
    if not is_overload_accepted:
        # TH1: Hợp lý - Kỳ cuối chỉ làm tốt nghiệp
        target_cre = (total_cre - grad_cre) / (num_semesters - 1)
    else:
        # TH2: Chấp nhận quá tải - Dàn đều tất cả các kỳ
        target_cre = total_cre / num_semesters

    semesters = {i: [] for i in range(1, num_semesters + 1)}  # Khởi tạo danh sách học kì
    completed_courses = set() # Tạo tập hợp set để lưu mã môn đã hoàn thành
    elective_tracker = {} # Theo dõi số tín chỉ đã chọn cho mỗi khối tự chọn
    
    # --- 1. XỬ LÝ KỲ CUỐI ---
    graduation_courses = [c for c in all_courses if "khóa luận tốt nghiệp" in c.get('khoi_kien_thuc', '').lower()] # học phnầ khóa luận hoặc thay thế khóa luận sẽ được xếp vào kỳ cuối cùng
    grad_ids = {c['ma_hp'] for c in graduation_courses} #Lấy ra mã học phần của học phần tốt nghiệp
    
    if graduation_courses:
        grad_credits = int(graduation_courses[0]['tin_chi'])
        semesters[num_semesters] = [{
            "ma_hp": "KLTN_SUM",
            "ten_hp_vi": "Khóa luận tốt nghiệp hoặc các học phần thay thế",
            "tin_chi": grad_credits,
            "khoi_kien_thuc": "Khối kiến thức tốt nghiệp"
        }]

    # --- 2. CHUẨN BỊ DANH SÁCH ---
    study_list = [c for c in all_courses if c['ma_hp'] not in grad_ids] # Loại bỏ học phần tốt nghiệp khỏi danh sách chính để xếp ở kỳ cuối

    # Hàm kiểm tra môn có phải là tiên quyết của môn khác không (để ưu tiên)
    def is_prereq_for_others(course_id, remaining_list):
        for c in remaining_list:
            if course_id in c.get('tien_quyet', ""):
                return True
        return False
    
    for sem in range(1, num_semesters + 1):
        # Nếu là TH1 và là kỳ cuối -> Bỏ qua vì đã xếp tốt nghiệp ở trên
        if not is_overload_accepted and sem == num_semesters:
            continue

        current_credits = 0
        remaining = []
        added_this_sem = []
        
        # Sắp xếp lại study_list mỗi kỳ: Ưu tiên môn bắt buộc trước, môn tự chọn sau
        study_list.sort(key=lambda x: (parse_knowledge_block(x.get("khoi_kien_thuc", ""))[1], int(x["tin_chi"])))

        for course in study_list:
            raw_block = course.get("khoi_kien_thuc", "")
            clean_name, required, available = parse_knowledge_block(raw_block)
            picked = 0

            # KIỂM TRA ĐỊNH MỨC (Chỉ áp dụng nếu là môn tự chọn, tức required > 0)
            is_elective = required < available and required > 0
            picked = elective_tracker.get(clean_name, 0) if is_elective else 0
            
            # KIỂM TRA TIÊN QUYẾT & TÍN CHỈ
            # ... (Logic can_take và giới hạn max_cre giữ nguyên) ...
            raw_pre = course.get('tien_quyet', "")
            prereqs = [p.strip() for p in raw_pre.split(',') if p.strip()]
            can_take = all(pr in completed_courses for pr in prereqs) #Môn nay có thể được chọn hay không
            cre = int(course["tin_chi"])
            
            if can_take and (current_credits + cre <= max_cre):

                if is_elective and picked >= required:
                    continue

                # LỚP 1: CHẶN TRẦN (MAX) - Nếu thêm vào mà vượt Max thì phải để kỳ sau
                if current_credits + cre > max_cre:
                    remaining.append(course)
                    continue
                
                # LỚP 2: ĐẢM BẢO SÀN (MIN)
                # Nếu chưa đạt min_cre, ta ép nhặt mọi môn có thể
                if current_credits < min_cre:
                    pass
                # Loại bỏ "để dành" môn lớn, luôn thêm nếu có thể
                # elif current_credits >= min_cre and sem < num_semesters:
                #     if not is_prereq_for_others(course['ma_hp'], study_list) and cre > 2:
                #         remaining.append(course)
                #         continue

                semesters[sem].append({
                    "ma_hp": course["ma_hp"],
                    "ten_hp_vi": course["ten_hp_vi"],
                    "tin_chi": cre,
                    "khoi_kien_thuc": course["khoi_kien_thuc"],
                    "tien_quyet": course["tien_quyet"]
                })
                current_credits += cre
                added_this_sem.append(course["ma_hp"])
                
                # Cập nhật bộ đếm nếu là môn tự chọn
                if is_elective:
                    elective_tracker[clean_name] = picked + cre
            else:
                remaining.append(course)
        
        # BỔ SUNG: ĐẢM BẢO MIN_CRE - Nếu kỳ này chưa đạt min, cố gắng thêm môn từ remaining (bỏ qua tiên quyết nếu cần)
        while current_credits < min_cre and remaining:
            for i, course in enumerate(remaining):
                cre = int(course["tin_chi"])
                raw_block = course.get("khoi_kien_thuc", "")
                clean_name, required, available = parse_knowledge_block(raw_block)
                is_elective = required < available and required > 0
                picked = elective_tracker.get(clean_name, 0) if is_elective else 0
                
                if (current_credits + cre <= max_cre) and (not is_elective or picked < required):
                    semesters[sem].append({
                        "ma_hp": course["ma_hp"],
                        "ten_hp_vi": course["ten_hp_vi"],
                        "tin_chi": cre,
                        "khoi_kien_thuc": course["khoi_kien_thuc"],
                        "tien_quyet": course["tien_quyet"]
                    })
                    current_credits += cre
                    added_this_sem.append(course["ma_hp"])
                    if is_elective:
                        elective_tracker[clean_name] = picked + cre
                    remaining.pop(i)
                    break
            else:
                # Nếu không thêm được môn nào nữa, dừng
                break
        
        for ma in added_this_sem:
            completed_courses.add(ma)
            
        study_list = remaining
            
    return semesters

def get_curriculum_metrics(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 1. Trích xuất tổng tín chỉ từ văn bản tóm tắt
    raw_summary = data["phan_3_khung_chuong_trinh_dao_tao"]["1_tom_tat_yeu_cau"]
    if isinstance(raw_summary, dict):
        summary_text = ", ".join([f"{k.replace('_', ' ')} là {v}" for k, v in raw_summary.items()])
    else:
        summary_text = str(raw_summary)

    total_match = re.search(r"(\d+)\s+tín chỉ", summary_text)
    total_required = int(total_match.group(1)) if total_match else 129
    
    # 2. Lấy danh sách môn học
    all_courses = data["phan_3_khung_chuong_trinh_dao_tao"]["2_danh_sach_hoc_phan"]
    
    # 3. Tự động nhận diện nhóm môn tốt nghiệp
    # Lọc dựa trên khối kiến thức hoặc tên học phần có chữ "tốt nghiệp"
    grad_courses = [
        c for c in all_courses 
        if "tốt nghiệp" in c.get('khoi_kien_thuc', '').lower() 
        or "tốt nghiệp" in c.get('ten_hp_vi', '').lower()
    ]
    grad_credits = sum(int(c['tin_chi']) for c in grad_courses)
    
    # 4. Lấy tên ngành để hiển thị giao diện
    major_name = data["phan_1_thong_tin_chung"]["1_mot_so_thong_tin_chuong_trinh_dao_tao"]["ten_nganh_vi"]
    
    return {
        "total": total_required,
        "grad_cre": grad_credits,
        "courses": all_courses,
        "major": major_name
    }