import streamlit as st
import json
import pandas as pd
import re
import os
from modules.rag_pipeline import rag_pipeline

# Hàm bóc tách tín chỉ định mức của từng khối kiến thức

def parse_knowledge_block(block_name):
    if not block_name:
        return "", 0, 0
        
    block_name = str(block_name).strip()
    # Xóa phần ngoặc đơn cuối cùng để lấy tên sạch
    clean_name = re.sub(r"\s*\([^()]*\)\s*$", "", block_name).strip()

    paren_match = re.search(r"\(([^()]+)\)\s*$", block_name)
    if paren_match:
        inside = paren_match.group(1).lower()
        
        # Trường hợp 1: Có định dạng rõ ràng dạng x/y (Ví dụ: 4/8 hoặc Tự chọn: 4/8)
        match_slash = re.search(r"(\d+)\s*/\s*(\d+)", inside)
        if match_slash:
            return clean_name, int(match_slash.group(1)), int(match_slash.group(2))
            
        # Trường hợp 2: Có chữ "tự chọn" và đi kèm một con số duy nhất
        # Ví dụ: "tự chọn 6 tín chỉ" hoặc "tự chọn 3 học phần"
        match_num = re.search(r"(?:tự chọn|yêu cầu)\s*(\d+)", inside)
        if match_num:
            num = int(match_num.group(1))
            if "học phần" in inside or "môn" in inside:
                # Nếu VNU ghi số môn/học phần, ta ước lượng tạm thời (ví dụ x3), 
                # nhưng lát nữa hàm cấu hình cấu trúc sẽ ghi đè lại dựa trên thực tế môn học.
                return clean_name, num * 3, 999 
            return clean_name, num, 999

    # Nếu không phải khối tự chọn (bắt buộc)
    return clean_name, 0, 0

# Hàm xử lý tiên quyết hỗ trợ cả AND (,) và OR (/)
def parse_prerequisites(raw_pre_string, completed_courses):
    """
    Parse tiên quyết hỗ trợ cả AND (,) và OR (/).
    Nếu chứa /, coi là OR (ít nhất một phải hoàn thành).
    Nếu chỉ , coi là AND (tất cả phải hoàn thành).
    
    Args:
        raw_pre_string: Chuỗi tiên quyết từ JSON (VD: 'MAT2400 / MAT2501')
        completed_courses: Set các mã môn đã hoàn thành
    
    Returns:
        True nếu tiên quyết được thỏa, False nếu không
    """
    if not raw_pre_string:
        return True
    
    raw_pre_string = raw_pre_string.strip()
    if not raw_pre_string:
        return True
    
    if '/' in raw_pre_string:
        # Tiên quyết OR: ít nhất một phải hoàn thành
        options = [p.strip() for p in raw_pre_string.split('/') if p.strip()]
        return any(opt in completed_courses for opt in options)
    else:
        # Tiên quyết AND: tất cả phải hoàn thành
        prereqs = [p.strip() for p in raw_pre_string.split(',') if p.strip()]
        return all(pr in completed_courses for pr in prereqs)

# Trích xuất các khối kiến thức

def get_elective_configs(all_courses):
    """Xây dựng cấu hình khối kiến thức tự chọn từ danh sách môn học đầy đủ.

    Trả về danh sách các khối với tín chỉ yêu cầu, tín chỉ có sẵn và các môn học chứa trong đó.
    """
    elective_configs = []
    
    # 1. Lấy danh sách các tên khối kiến thức độc nhất
    unique_blocks = sorted(list(set(c['khoi_kien_thuc'] for c in all_courses)))
    
    for block_name in unique_blocks:
        # SỬ DỤNG HÀM CỦA BẠN TẠI ĐÂY
        clean_name, required, available = parse_knowledge_block(block_name)
        
        # Chỉ lấy khối tự chọn, tức required < available
        if required > 0 and required < available:
            # Lọc danh sách môn thuộc khối này
            courses_in_block = [c for c in all_courses if c['khoi_kien_thuc'] == block_name]
            
            # Tính toán số tín thực tế đang có trong danh sách môn
            actual_total_cre = sum(int(c['tin_chi']) for c in courses_in_block)
            is_elective = (required > 0 and required < actual_total_cre) or "tự chọn" in block_name.lower()

            if is_elective:
            # Nếu required bị nhận diện sai hoặc bằng 999 (do chuỗi không chuẩn), 
            # đặt mặc định bằng một nửa tổng số môn hoặc một giá trị an toàn
                if required == 0 or required >= actual_total_cre:
                    required = actual_total_cre // 2

                elective_configs.append({
                    "block_name": clean_name,        # Tên sạch để hiển thị UI
                    "raw_name": block_name,         # Tên gốc để map dữ liệu
                    "required_credits": required,   # Số tín chỉ sinh viên cần chọn (ví dụ 12)
                    "declared_total": available,    # Số tín chỉ được ghi trong ngoặc (ví dụ 8 trong 4/8)
                    "total_available": actual_total_cre, # Tổng số tín chỉ có sẵn trong file (ví dụ 39)
                    "courses": courses_in_block
                })
            
    return elective_configs

def calculate_advanced_prereq_weights(all_courses):
    """Tính toán đệ quy xem một môn học làm tiên quyết cho bao nhiêu môn phía sau."""
    graph = {}
    for c in all_courses:
        ma_hp = c.get("ma_hp")
        raw_pre = c.get("tien_quyet", "")
        if raw_pre and str(raw_pre) != "nan":
            # Tách các mã môn tiên quyết bằng các dấu phân tách phổ biến
            pre_ids = [p.strip() for p in re.split(r'[/,;\s]+', str(raw_pre)) if p.strip()]
            for pre in pre_ids:
                if pre not in graph:
                    graph[pre] = set()
                graph[pre].add(ma_hp)
                
    weights = {}
    def dfs(node, visited):
        if node not in graph:
            return 0
        total_affected = 0
        for child in graph[node]:
            if child not in visited:
                visited.add(child)
                total_affected += 1 + dfs(child, visited)
        return total_affected

    for c in all_courses:
        ma_hp = c.get("ma_hp")
        weights[ma_hp] = dfs(ma_hp, set())
    return weights

# 1. Hàm logic phân bổ lộ trình (đầu vào là JSON)
def generate_study_plan_range(all_courses, min_cre, max_cre, num_semesters, metrics, is_overload_accepted = False):
    """Tạo lộ trình học tập dàn đều theo từng kỳ từ dữ liệu môn học và quy tắc tín chỉ."""
    total_cre = metrics["total"]
    print(f"TOTAL CREDITS REQUIRED: {total_cre}")
    grad_cre = metrics["grad_cre"]
    print(f"GRADUATION CREDITS: {grad_cre}")

    # XÁC ĐỊNH TARGET CREDITS (Mục tiêu tín chỉ lý tưởng cho mỗi kỳ)
    if not is_overload_accepted:
        target_cre = (total_cre - grad_cre + 3) / (num_semesters - 1)
    else:
        target_cre = (total_cre + 3) / num_semesters
        
    print(f"TARGET CREDITS PER SEMESTER: {target_cre:.2f}")

    semesters = {i: [] for i in range(1, num_semesters + 1)}  
    completed_courses = set() 
    elective_tracker = {} 
    
    # 0. KHỞI TẠO TỪ ĐIỂN TRA CỨU KHỐI TỰ CHỌN (ĐẶT Ở NGOÀI VÒNG LẶP - CHUẨN ĐƠN VỊ TÍN CHỈ)
    elective_requirements = {cfg["raw_name"]: cfg["required_credits"] for cfg in get_elective_configs(all_courses)}
    
    # --- 1. XỬ LÝ KỲ CUỐI ---
    graduation_courses = [c for c in all_courses if "khóa luận tốt nghiệp" in c.get('khoi_kien_thuc', '').lower()]
    grad_ids = {c['ma_hp'] for c in graduation_courses}
    
    if graduation_courses:
        grad_credits = int(graduation_courses[0]['tin_chi'])
        semesters[num_semesters] = [{
            "ma_hp": "KLTN_SUM",
            "ten_hp_vi": "Khóa luận tốt nghiệp hoặc các học phần thay thế",
            "tin_chi": grad_credits,
            "khoi_kien_thuc": "Khối kiến thức tốt nghiệp"
        }]

    # --- 2. CHUẨN BỊ DANH SÁCH ---
    study_list = [c for c in all_courses if c['ma_hp'] not in grad_ids]

    prereq_weights = calculate_advanced_prereq_weights(all_courses)
    # Định nghĩa hàm sắp xếp sử dụng từ điển tra cứu thống nhất
    def _block_sort_key(x):
        raw_block = x.get("khoi_kien_thuc", "")
        clean_name, req_cre, total_pool = parse_knowledge_block(raw_block)
        ma_hp = x.get("ma_hp", "")
    
        # Lấy trọng số mở đường của môn học (môn gánh nhiều môn phía sau thì số càng lớn)
        p_weight = prereq_weights.get(ma_hp, 0)
    
        # Tiêu chí 1: Phân nhóm dựa vào tính chất Khối kiến thức
        if req_cre == 0 and total_pool == 0:
            group_priority = 0  # Môn bắt buộc/môn chung ưu tiên lên đầu
        else:
            group_priority = req_cre if req_cre > 0 else 999  # Môn tự chọn lùi sau
        
        # SỬA TẠI ĐÂY: Thay vì trả về mã môn học, trả về -p_weight để môn gánh tiên quyết nặng nhất lên đầu hàng đợi
        return (group_priority, -p_weight, -int(x.get("tin_chi", 0)))


    def _course_sort_key(course):
        """Hàm sort dùng cho các lượt cứu trợ (Lượt 2, 3, 4) trong code của bạn"""
        raw_block = course.get("khoi_kien_thuc", "")
        clean_name, req_cre, total_pool = parse_knowledge_block(raw_block)
        ma_hp = course.get("ma_hp", "")
        p_weight = prereq_weights.get(ma_hp, 0)
    
        group_priority = 0 if (req_cre == 0 and total_pool == 0) else 1
        # Ưu tiên bắt buộc trước, môn gánh tiên quyết nhiều xếp trước, tín chỉ nặng xếp trước
        return (group_priority, -p_weight, -int(course.get("tin_chi", 0)))

    for sem in range(1, num_semesters + 1):
        if sem == num_semesters and not is_overload_accepted:
            continue
            
        current_credits = sum(int(c["tin_chi"]) for c in semesters[sem])
        remaining = []
        added_this_sem = []
        
        study_list.sort(key=_block_sort_key)
        
        # LƯỢT 1: PHÂN BỔ ĐẾN MAX_CRE (ƯU TIÊN MÔN BẮT BUỘC/TỰ CHỌN CHƯA ĐỦ ĐỊNH MỨC)
        for course in study_list:
            block_key = course.get("khoi_kien_thuc", "")
            is_elective = block_key in elective_requirements
            required = elective_requirements.get(block_key, 0)
            picked = elective_tracker.get(block_key, 0)
            
            raw_pre = course.get('tien_quyet', "")
            can_take = parse_prerequisites(raw_pre, completed_courses)
            cre = int(course["tin_chi"])
            
            if can_take:
                if is_elective and picked + cre > required:
                    remaining.append(course)
                    continue

                if current_credits + cre > max_cre:
                    remaining.append(course)
                    continue

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
                    elective_tracker[block_key] = picked + cre
            else:
                remaining.append(course)
        
        # LƯỢT 2: CỨU TRỢ CHẶN SÀN MIN_CRE
        if current_credits < min_cre:
            while current_credits < min_cre:
                added_any = False
                for course in sorted(remaining, key=_course_sort_key):
                    block_key = course.get("khoi_kien_thuc", "")
                    is_elective = block_key in elective_requirements
                    required = elective_requirements.get(block_key, 0)
                    picked = elective_tracker.get(block_key, 0)

                    raw_pre = course.get('tien_quyet', "")
                    # SỬA LỖI: Đồng bộ dùng hàm parse chuẩn hỗ trợ AND/OR
                    can_take = parse_prerequisites(raw_pre, completed_courses)
                    cre = int(course["tin_chi"])

                    if can_take and (current_credits + cre <= max_cre) and (not is_elective or picked + cre <= required):
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
                            elective_tracker[block_key] = picked + cre
                        remaining = [c for c in remaining if c["ma_hp"] != course["ma_hp"]]
                        added_any = True
                        break
                if not added_any:
                    break

        # LƯỢT 3: CỐ GẮNG ĐẠT TARGET_CRE
        while current_credits < target_cre:
            added_any = False
            for course in sorted(remaining, key=_course_sort_key):
                block_key = course.get("khoi_kien_thuc", "")
                is_elective = block_key in elective_requirements
                required = elective_requirements.get(block_key, 0)
                picked = elective_tracker.get(block_key, 0)

                raw_pre = course.get('tien_quyet', "")
                # SỬA LỖI: Đồng bộ dùng hàm parse chuẩn
                can_take = parse_prerequisites(raw_pre, completed_courses)
                cre = int(course["tin_chi"])

                if can_take and (current_credits + cre <= target_cre) and (not is_elective or picked + cre <= required):
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
                        elective_tracker[block_key] = picked + cre
                    remaining = [c for c in remaining if c["ma_hp"] != course["ma_hp"]]
                    added_any = True
                    break
            if not added_any:
                break

        # LƯỢT 4: FILL TO MAX
        while current_credits < max_cre:
            added_any = False
            for course in sorted(remaining, key=_course_sort_key):
                block_key = course.get("khoi_kien_thuc", "")
                is_elective = block_key in elective_requirements
                required = elective_requirements.get(block_key, 0)
                picked = elective_tracker.get(block_key, 0)

                raw_pre = course.get('tien_quyet', "")
                # SỬA LỖI: Đồng bộ dùng hàm parse chuẩn
                can_take = parse_prerequisites(raw_pre, completed_courses)
                cre = int(course["tin_chi"])

                if can_take and (current_credits + cre <= max_cre) and (not is_elective or picked + cre <= required):
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
                        elective_tracker[block_key] = picked + cre
                    remaining = [c for c in remaining if c["ma_hp"] != course["ma_hp"]]
                    added_any = True
                    break
            if not added_any:
                break

        # Cập nhật trạng thái học tập sau kỳ
        for ma in added_this_sem:
            completed_courses.add(ma)
            
        study_list = remaining
    
    # ====== LƯỢT 5: XỬ LÝ DỰ THỪA (SỬA LỖI CHẶN MÔN TỰ CHỌN QUÁ ĐỊNH MỨC) ======
    luat5_iteration = 0
    while study_list and luat5_iteration < 10:
        luat5_iteration += 1
        
        semesters_with_space = []
        for sem in range(1, num_semesters):  
            current_sem_cre = sum(int(c["tin_chi"]) for c in semesters[sem])
            space = max_cre - current_sem_cre
            if space > 0:
                semesters_with_space.append((sem, space, current_sem_cre))
        
        if not semesters_with_space:
            break
        
        semesters_with_space.sort(key=lambda x: x[1])
        
        placed_count = 0
        new_study_list = []
        for course in study_list:
            cre = int(course["tin_chi"])
            block_key = course.get("khoi_kien_thuc", "")
            
            is_elective = block_key in elective_requirements
            required = elective_requirements.get(block_key, 0)
            picked = elective_tracker.get(block_key, 0)
            
            # SỬA LỖI CHÍNH TẠI ĐÂY: Nếu khối tự chọn này đã đủ chỉ tiêu tích lũy, 
            # loại bỏ hẳn học phần thừa này, tuyệt đối không xếp "vét" vào lộ trình nữa.
            if is_elective and picked + cre > required:
                continue # Bỏ qua hoàn toàn, sinh viên không cần học môn này để tốt nghiệp
            
            raw_pre = course.get('tien_quyet', "")
            
            placed = False
            for sem_idx, (sem, space, sem_cre) in enumerate(semesters_with_space):
                # Sử dụng hàm parse chuẩn hỗ trợ AND/OR
                can_take = parse_prerequisites(raw_pre, completed_courses)
                if can_take and cre <= space:
                    semesters[sem].append({
                        "ma_hp": course["ma_hp"],
                        "ten_hp_vi": course["ten_hp_vi"],
                        "tin_chi": cre,
                        "khoi_kien_thuc": course["khoi_kien_thuc"],
                        "tien_quyet": course["tien_quyet"]
                    })
                    completed_courses.add(course["ma_hp"])
                    semesters_with_space[sem_idx] = (sem, space - cre, sem_cre + cre)
                    placed = True
                    placed_count += 1
                    if is_elective:
                        elective_tracker[block_key] = picked + cre
                    break
            
            if not placed:
                new_study_list.append(course)
        
        if placed_count == 0:
            break
        
        study_list = new_study_list
            
    return semesters
def get_curriculum_metrics(file_path):
    """Đọc file JSON chương trình đào tạo và trích xuất tín chỉ tổng, tín chỉ tốt nghiệp, danh sách môn học và tên ngành."""
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
        if "khóa luận tốt nghiệp" in c.get('ten_hp_vi', '').lower() 
        
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


def _find_course_by_name_or_code(query, all_courses):
    """Tìm môn học theo mã chính xác hoặc tên tiếng Việt một phần từ dữ liệu môn học."""
    query_lower = query.lower().strip()
    # Tìm theo mã môn trước
    for course in all_courses:
        if course.get("ma_hp", "").lower() == query_lower:
            return course
    # Tìm theo tên môn
    for course in all_courses:
        if query_lower in course.get("ten_hp_vi", "").lower():
            return course
    return None

def _is_course_in_plan(course_code, plan):
    """Trả về True nếu mã môn học đã tồn tại trong lộ trình hiện tại."""
    for sem, courses in plan.items():
        for course in courses:
            if course.get("ma_hp", "").strip().upper() == course_code.strip().upper():
                return True
    return False

def _find_course_semester(plan, course_code):
    """
    Tìm kiếm một mã môn học xem nó đang được xếp ở học kỳ mấy trong lộ trình hiện tại.
    Trả về số học kỳ (int) nếu tìm thấy, ngược lại trả về None nếu môn đó chưa được xếp lịch.
    """
    if not plan:
        return None
        
    course_code_clean = str(course_code).strip().upper()
    
    # Duyệt qua từng học kỳ và danh sách môn học của kỳ đó
    for sem, courses in plan.items():
        for course in courses:
            if course.get("ma_hp", "").strip().upper() == course_code_clean:
                return int(sem)  # Trả về số học kỳ (ví dụ: 1, 2, 3...)
                
    return None

def _add_course_to_plan(plan, course, num_semesters, min_cre, max_cre, all_courses):
    """Chèn môn học vào kỳ sớm nhất khả thi mà tôn trọng tiên quyết và giới hạn tín chỉ."""

    """Thêm môn học vào lộ trình, tìm kỳ phù hợp"""
    if not course:
        return None
    
    course_code = course["ma_hp"]
    cre = int(course["tin_chi"])
    
    # Kiểm tra tiên quyết
    raw_pre = course.get("tien_quyet", "")
    prereqs = [p.strip().upper() for p in re.split(r'[;,\n]', raw_pre) if p.strip()]
    
    # Tìm kỳ phù hợp: kỳ sớm nhất mà tiên quyết đã hoàn thành và có chỗ trống
    for sem in range(1, num_semesters + 1):
        current_cre = sum(int(c["tin_chi"]) for c in plan.get(sem, []))
        
        # Kiểm tra tiên quyết
        prereqs_met = True
        for pr in prereqs:
            pr_sem = _find_course_semester(plan, pr)
            if pr_sem is None or pr_sem >= sem:
                prereqs_met = False
                break
        
        if prereqs_met and current_cre + cre <= max_cre:
            # Thêm vào kỳ này
            new_plan = {s: [c.copy() for c in courses] for s, courses in plan.items()}
            new_plan[sem].append({
                "ma_hp": course["ma_hp"],
                "ten_hp_vi": course["ten_hp_vi"],
                "tin_chi": cre,
                "khoi_kien_thuc": course["khoi_kien_thuc"],
                "tien_quyet": course["tien_quyet"]
            })
            return new_plan
    
    return None  # Không thể thêm


def _prereqs_met_for_semester(course, plan, target_sem):
    """Kiểm tra xem tất cả tiên quyết cho môn học có được lên lịch trước kỳ mục tiêu không."""
    raw_pre = course.get("tien_quyet", "")
    prereqs = [p.strip().upper() for p in re.split(r'[;,\n]', raw_pre) if p.strip()]
    if not prereqs:
        return True
    for pr in prereqs:
        sem = _find_course_semester(plan, pr)
        if sem is None or sem >= target_sem:
            return False
    return True


# Di chuyển môn học giữa hai kỳ
def _move_courses_between_semesters(plan, source_sem, dest_sem, desired_delta=None, max_cre=None):
    """Thử di chuyển môn học giữa hai kỳ trong khi tôn trọng tiên quyết và giới hạn tín chỉ tối đa."""
    source_list = plan.get(source_sem, [])[:]
    dest_list = plan.get(dest_sem, [])[:]
    current_source = sum(int(c["tin_chi"]) for c in source_list)
    current_dest = sum(int(c["tin_chi"]) for c in dest_list)

    if max_cre is None:
        max_cre = 999

    if desired_delta is None:
        desired_delta = min(current_source, max(0, max_cre - current_dest))
        if desired_delta <= 0:
            return None

    candidates = [c for c in source_list if _prereqs_met_for_semester(c, plan, dest_sem)]
    candidates.sort(key=lambda c: int(c.get("tin_chi", 0)))

    selected = []
    moved_credits = 0
    for course in candidates:
        cre = int(course.get("tin_chi", 0))
        if current_dest + moved_credits + cre > max_cre:
            continue
        selected.append(course)
        moved_credits += cre
        if moved_credits >= desired_delta:
            break

    if not selected:
        return None

    new_plan = {sem: [c for c in courses] for sem, courses in plan.items()}
    for course in selected:
        new_plan[source_sem] = [c for c in new_plan[source_sem] if c.get("ma_hp") != course.get("ma_hp")]
        new_plan[dest_sem].append(course)

    return new_plan

# Tư vấn Mentor
def _parse_plan_adjustment_instructions(question):
    """Trích xuất hướng dẫn điều chỉnh tín chỉ kỳ từ câu hỏi tự do của người dùng."""
    text = question.lower()
    adjustments = []

    # Pattern 1: "tăng/giảm (số) tín chỉ (của|cho) kỳ X (lên|còn) Y"
    pattern1 = re.compile(
        r'(?P<verb>giảm|tăng)\s+(?:số\s+)?tín\s+chỉ(?:\s*(?:của|cho|ở))?\s*(?:học\s*)?kỳ\s*(?P<sem>\d+)(?:\s*(?:còn|lên)\s*(?P<value>\d+))?',
        re.I,
    )
    
    # Pattern 2: "kỳ X (tăng|giảm) lên/xuống Y tín" hoặc "kỳ X tăng/giảm"
    pattern2 = re.compile(
        r'(?:học\s*)?kỳ\s*(?P<sem>\d+)\s+(?P<verb>tăng|giảm)(?:\s+(?:lên|xuống)\s+(?P<value>\d+)\s*tín)?',
        re.I,
    )

    for pattern in [pattern1, pattern2]:
        for match in pattern.finditer(text):
            verb = match.group("verb")
            sem = int(match.group("sem"))
            value = match.group("value")
            adjustments.append({
                "verb": verb,
                "semester": sem,
                "target_credits": int(value) if value else None,
            })

    if not adjustments:
        # Fallback: "tăng/giảm tín chỉ" mà không chỉ định kỳ
        simple_pattern = re.compile(r'(?P<verb>giảm|tăng)\s+(?:số\s+)?tín\s+chỉ', re.I)
        for match in simple_pattern.finditer(text):
            adjustments.append({
                "verb": match.group("verb"),
                "semester": None,
                "target_credits": None,
            })

    return adjustments


def _adjust_current_plan(current_plan, instructions, max_cre):
    """Điều chỉnh lộ trình hiện tại bằng cách di chuyển môn học dựa trên hướng dẫn tăng/giảm đã phân tích."""
    if not current_plan or not instructions:
        return None

    decrease = next((ins for ins in instructions if ins["verb"] == "giảm" and ins.get("semester") is not None), None)
    increase = next((ins for ins in instructions if ins["verb"] == "tăng" and ins.get("semester") is not None), None)

    if decrease and increase:
        source_sem = decrease["semester"]
        dest_sem = increase["semester"]
        if source_sem == dest_sem:
            return None

        current_source = sum(int(c["tin_chi"]) for c in current_plan.get(source_sem, []))
        current_dest = sum(int(c["tin_chi"]) for c in current_plan.get(dest_sem, []))

        desired_delta = None
        if decrease["target_credits"] is not None:
            desired_delta = max(0, current_source - decrease["target_credits"])
        elif increase["target_credits"] is not None:
            desired_delta = max(0, increase["target_credits"] - current_dest)

        adjusted = _move_courses_between_semesters(current_plan, source_sem, dest_sem, desired_delta, max_cre=max_cre)
        if adjusted:
            return adjusted

    single = decrease or increase
    if single and single.get("semester") is not None:
        sem = single["semester"]
        if decrease:
            target_sem = sem + 1 if sem + 1 in current_plan else sem - 1
            if target_sem in current_plan:
                return _move_courses_between_semesters(current_plan, sem, target_sem, None, max_cre=max_cre)
        else:
            source_sem = sem - 1 if sem - 1 in current_plan else sem + 1
            if source_sem in current_plan:
                return _move_courses_between_semesters(current_plan, source_sem, sem, None, max_cre=max_cre)

    return None


def _clean_json_text(text):
    """Làm sạch đầu ra văn bản mô hình để trích xuất nội dung JSON thô một cách an toàn."""
    if not text:
        return ""
    text = text.strip()
    # Loại bỏ code block Markdown nếu có
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _extract_model_text_response(response):
    """Trích xuất phản hồi văn bản dễ đọc từ đầu ra client LLM."""
    if response is None:
        return ""
    if hasattr(response, "text") and response.text:
        return str(response.text).strip()
    if hasattr(response, "output_text") and response.output_text:
        return str(response.output_text).strip()
    if hasattr(response, "candidates") and response.candidates:
        first = response.candidates[0]
        if hasattr(first, "content") and first.content:
            if isinstance(first.content, list):
                return "".join(str(c) for c in first.content).strip()
            return str(first.content).strip()
        if hasattr(first, "text") and first.text:
            return str(first.text).strip()
    return ""


def _format_plan_change_answer(current_plan, new_plan, decrease, increase):
    """Định dạng thông điệp hướng tới người dùng mô tả kết quả của việc điều chỉnh lộ trình."""
    if not new_plan:
        return "Xin lỗi, tôi không thể thay đổi lộ trình theo yêu cầu đó với cấu trúc hiện tại. Vui lòng thử lại bằng cách nói rõ kỳ hoặc số tín chỉ cần thay đổi."

    answer = "Đã cập nhật lộ trình theo yêu cầu của bạn."
    if decrease and increase:
        answer += f" Tôi đã chuyển một số tín chỉ từ kỳ {decrease['semester']} sang kỳ {increase['semester']} nếu có thể."
    elif decrease:
        answer += f" Tôi đã cố gắng giảm tín chỉ ở kỳ {decrease['semester']} và chuyển sang kỳ kế bên."
    elif increase:
        answer += f" Tôi đã cố gắng tăng tín chỉ ở kỳ {increase['semester']} bằng cách dịch chuyển môn học."
    answer += "\n\nBạn có thể xem lại lộ trình ở các tab học kỳ."
    return answer


def mentor_pipeline(mentor_question, selected_nganh, num_semesters, min_c, max_c, total_cre, grad_cre, current_plan, collection, client, all_courses_raw):
    """
    Xử lý câu hỏi mentor của sinh viên và trả về phản hồi cộng với lộ trình cập nhật tùy chọn.
    """
    MODEL_NAME = os.getenv("MODEL_NAME")
    
    intent_prompt = f"""
Bạn là trợ lý phân tích ý định của câu hỏi sinh viên trong hệ thống tư vấn lộ trình học tập ngành {selected_nganh}.

Câu hỏi của sinh viên: "{mentor_question}"

Hãy phân tích kỹ câu hỏi và trả về CHỈ MỘT chuỗi JSON duy nhất theo định dạng dưới đây. 
Nếu sinh viên nhắc đến tên môn học (ví dụ: "Cấu trúc dữ liệu", "Mạng máy tính"), hãy cố gắng đối chiếu và điền MÃ HỌC PHẦN (ví dụ: INT2203) nếu bạn biết, hoặc điền tên tiếng Việt chính xác của môn đó vào mảng "courses".

Định dạng JSON bắt buộc:
{{
    "intent": "information|calculation|advice|adjustment",
    "action": "none|recalculate|adjust_plan|add_course|remove_course",
    "courses": ["Mã hoặc tên môn học cần thêm/bớt nếu có"],
    "adjustments": [
        {{"verb": "giảm|tăng", "semester": 2, "target_credits": 18}}
    ],
    "reason": "Giải thích ngắn gọn tại sao phân loại như vậy"
}}
"""

    intent_data = {}
    try:
        intent_response = client.models.generate_content(
            model=MODEL_NAME,
            contents=intent_prompt
        )
        intent_text = _extract_model_text_response(intent_response)
        intent_text = _clean_json_text(intent_text)
        if intent_text:
            try:
                intent_data = json.loads(intent_text)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', intent_text, re.S)
                if match:
                    intent_data = json.loads(match.group(0))
                else:
                    raise
        else:
            raise ValueError("Empty intent response")
    except Exception as e:
        print(f"Lỗi phân tích intent: {e}")
        intent_data = {"intent": "advice", "action": "none"}

    intent = intent_data.get("intent", "advice")
    action = intent_data.get("action", "none")
    adjustments = intent_data.get("adjustments", []) or []
    courses = intent_data.get("courses", []) or []

    # SỬA LỖI KHỚP MÔN: Dùng kết quả trích xuất thông minh từ LLM trước, nếu trống mới fallback
    if action == "add_course" or (not courses and ("thêm" in mentor_question.lower() or "thiếu" in mentor_question.lower())):
        # Nếu LLM không ra mã, ta tìm kiếm thực thể dựa trên các cụm từ quan trọng trong câu hỏi
        if not courses:
            # Fallback quét thô qua câu hỏi nếu LLM bỏ sót cụm từ
            potential_courses = []
            for course in all_courses_raw:
                if course["ma_hp"].lower() in mentor_question.lower() or course["ten_hp_vi"].lower() in mentor_question.lower():
                    potential_courses.append(course["ma_hp"])
            courses = potential_courses
            if courses:
                action = "add_course"

    # --- 2. XỬ LÝ HÀNH ĐỘNG: THÊM MÔN HỌC ---
    if action == "add_course" or courses:
        if not current_plan:
            return {"answer": "Hiện tại bạn chưa có lộ trình học nào để thêm môn. Vui lòng tạo lộ trình trước.", "updated_plan": None}
        
        added_courses = []
        failed_courses = []
        new_plan = {s: [c.copy() for c in courses_list] for s, courses_list in current_plan.items()}
        
        for course_query in courses:
            course = _find_course_by_name_or_code(course_query, all_courses_raw)
            if not course:
                failed_courses.append(course_query)
                continue
            
            if _is_course_in_plan(course["ma_hp"], new_plan):
                failed_courses.append(f"{course['ten_hp_vi']} (Đã có trong lộ trình)")
                continue
            
            updated = _add_course_to_plan(new_plan, course, num_semesters, min_c, max_c, all_courses_raw)
            if updated:
                new_plan = updated
                added_courses.append(f"**{course['ten_hp_vi']} ({course['ma_hp']})**")
            else:
                failed_courses.append(f"{course['ten_hp_vi']} (Không xếp được do vướng môn tiên quyết hoặc kỳ học đã đạt trần {max_c} tín)")
        
        if added_courses:
            answer = f"🎯 **Mentor đã chèn thành công môn học vào kỳ sớm nhất phù hợp cho em:**\n"
            answer += f"✅ Đã thêm: {', '.join(added_courses)}.\n\n"
            answer += "📊 **Cập nhật số lượng tín chỉ mới các kỳ:**\n"
            for sem in range(1, num_semesters + 1):
                sem_cre = sum(int(c["tin_chi"]) for c in new_plan.get(sem, []))
                answer += f"- Học kỳ {sem}: {sem_cre} tín chỉ\n"
            return {"answer": answer, "updated_plan": new_plan}
        else:
            answer = "⚠️ Không thể thêm môn học yêu cầu vào lộ trình hiện tại.\n"
            if failed_courses:
                answer += f"Chi tiết lý do: {', '.join(failed_courses)}"
            return {"answer": answer, "updated_plan": None}

    # --- 3. XỬ LÝ HÀNH ĐỘNG: ĐIỀU CHỈNH DỊCH CHUYỂN TÍN CHỈ KỲ ---
    if action == "adjust_plan":
        if not current_plan:
            return {"answer": "Hiện tại bạn chưa có lộ trình học nào để điều chỉnh. Vui lòng tạo lộ trình trước.", "updated_plan": None}

        if not adjustments:
            adjustments = _parse_plan_adjustment_instructions(mentor_question)

        # SỬA LOGIC DI CHUYỂN: Sử dụng hàm tinh gọn cục bộ thay vì regenerate phá vỡ khung gối đầu
        decrease = next((ins for ins in adjustments if ins["verb"] == "giảm" and ins.get("semester") is not None), None)
        increase = next((ins for ins in adjustments if ins["verb"] == "tăng" and ins.get("semester") is not None), None)
        
        new_plan = _adjust_current_plan(current_plan, adjustments, max_cre=max_c)
        
        if new_plan:
            answer = f"✅ **Thầy đã điều chỉnh dịch chuyển môn giữa các kỳ theo mong muốn của em:**\n"
            answer += _format_plan_change_answer(current_plan, new_plan, decrease, increase) + "\n\n"
            answer += "📈 **Tiến độ tín chỉ sau khi dịch chuyển:**\n"
            for sem in range(1, num_semesters + 1):
                sem_total = sum(int(c["tin_chi"]) for c in new_plan.get(sem, []))
                answer += f"- Học kỳ {sem}: {sem_total} tín chỉ\n"
            return {"answer": answer, "updated_plan": new_plan}
        else:
            return {
                "answer": f"⚠️ Thầy đã thử dịch chuyển môn để điều chỉnh nhưng không thành công. Nguyên nhân có thể do việc chuyển dịch vi phạm điều kiện môn tiên quyết, hoặc kỳ đích đã chạm mức giới hạn tối đa `{max_c}` tín chỉ.",
                "updated_plan": None
            }

    # --- 4. HÀNH ĐỘNG: TRA CỨU QUY CHẾ (RAG) ---
    if intent == "information":
        answer = rag_pipeline(mentor_question, collection, client)
        return {"answer": answer, "updated_plan": None}

    # --- 5. HÀNH ĐỘNG: TÍNH LẠI TOÀN BỘ (CALCULATION) ---
    if intent == "calculation":
        # Giữ nguyên phần tính toán lại của bạn vì nó chạy khá ổn định
        # ... (Phần logic bóc tách số kỳ / số tín chỉ mới từ regex của bạn) ...
        return {"answer": answer, "updated_plan": new_plan}

    # --- 6. HÀNH ĐỘNG: TƯ VẤN THẢO LUẬN (ADVICE) ---
    # Kết hợp ngữ cảnh RAG vào Advice prompt để Mentor trả lời sâu sắc hơn về chuyên môn
    rag_context = ""
    try:
        rag_context = rag_pipeline(mentor_question, collection, client)
    except:
        pass

    plan_summary = ""
    if current_plan:
        sem_summaries = []
        for i in range(1, num_semesters + 1):
            courses_names = [c['ten_hp_vi'] for c in current_plan.get(i, [])]
            sem_summaries.append(f"Học kỳ {i} ({sum(int(c['tin_chi']) for c in current_plan.get(i, []))} tín): {', '.join(courses_names)}")
        plan_summary = "\n".join(sem_summaries)
    else:
        plan_summary = "Chưa có lộ trình nào được tạo."

    advice_prompt = f"""
Bạn là Mentor (Cố vấn học tập) ngành {selected_nganh} tại trường VNU. Hãy nói chuyện thân thiện, xưng "Thầy" và gọi "Em".

Thông tin lộ trình hiện tại của sinh viên:
{plan_summary}

Quy chế hoặc thông tin bổ sung tra cứu được từ hệ thống văn bản của trường:
\"\"\"
{rag_context}
\"\"\"

Câu hỏi của sinh viên: "{mentor_question}"

Nhiệm vụ của bạn:
1. Trả lời chi tiết, chính xác thắc mắc của sinh viên.
2. Đối chiếu trực tiếp với danh sách các kỳ ở trên để chỉ rõ cho sinh viên thấy môn học họ đang lo lắng nằm ở học kỳ mấy, có nặng hay không, cần chuẩn bị kiến thức nền tảng gì.
3. Luôn giữ thái độ động viên, định hướng giải pháp rõ ràng.
"""
    try:
        advice_response = client.models.generate_content(
            model=MODEL_NAME,
            contents=advice_prompt
        )
        answer = _extract_model_text_response(advice_response)
    except Exception as e:
        error_text = str(e).lower()
        if "quota" in error_text or "resource_exhausted" in error_text or "429" in error_text:
            answer = "Hết quota"
        else:
            answer = f"Lỗi tạo tư vấn: {e}"

    return {"answer": answer, "updated_plan": None}