CATEGORY_URLS = {
    "itviec": "https://itviec.com/it-jobs",
    "topcv_soft": "https://topcv.vn/tim-viec-lam-it-phan-mem-c10026",
    "topcv_data": "https://topcv.vn/tim-viec-lam-du-lieu-c10041",
    "vw_it": "https://www.vietnamworks.com/viec-lam?q=&l=&ind=35",
    "jobstreet_it": "https://www.jobstreet.vn/jobs/in-information-technology",
}

LOCATIONS  = ["ho-chi-minh", "ha-noi", "da-nang", "remote"]
LEVELS     = ["intern-fresher", "junior", "senior-manager"]
TIME_RANGE = ["24h", "7-days"]

def build_filter_matrix():
    for loc in LOCATIONS:
        for level in LEVELS:
            for t in TIME_RANGE:
                yield {"location": loc, "level": level, "time": t}
                # Mỗi tổ hợp → ~10-20 trang → không bao giờ chạm trần