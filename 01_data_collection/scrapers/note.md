# Tài liệu thiết kế cơ sở dữ liệu hệ thống Job Crawl

## 1. Mục tiêu tài liệu

Tài liệu này mô tả thiết kế cơ sở dữ liệu cho hệ thống thu thập tin tuyển dụng theo hướng thực tế, dễ mở rộng, dễ vận hành và phù hợp với môi trường production. Thiết kế tập trung vào bốn mục tiêu chính: lưu dữ liệu crawl thô, chuẩn hóa dữ liệu job để phục vụ tìm kiếm và AI, kiểm soát pipeline xử lý, và đặc biệt là tránh crawl lại các job đã được thu thập trước đó.

Phạm vi của tài liệu bao gồm mô hình dữ liệu đề xuất, vai trò của từng bảng, khóa chính và khóa ngoại, chiến lược chống trùng lặp, chỉ mục cần thiết, luồng dữ liệu vận hành và các khuyến nghị triển khai thực tế.

## 2. Bối cảnh và vấn đề của thiết kế hiện tại

Thiết kế hiện tại đang có hai bảng chính là `crawl_queue` và `crawl_state`. Cách thiết kế này phù hợp cho giai đoạn proof of concept, nhưng khi hệ thống bắt đầu crawl nhiều nguồn, nhiều worker và có nhu cầu chuẩn hóa dữ liệu để search hoặc AI thì sẽ phát sinh các vấn đề sau:

- Một bảng đang gánh nhiều trách nhiệm cùng lúc, ví dụ vừa lưu thông tin định danh job, vừa lưu trạng thái xử lý AI, vừa lưu timestamp crawl.
- Queue chưa đủ thông tin để retry, lock theo worker, set priority hoặc phân biệt loại task.
- Dữ liệu raw và dữ liệu canonical chưa được tách riêng, dẫn đến khó debug parser và khó reprocess khi logic chuẩn hóa thay đổi.
- Chưa có chiến lược dedupe nhiều lớp để chống việc crawl trùng hoặc tạo nhiều bản ghi cho cùng một job.
- Các field location, salary, skills và processing state đang ở cùng một lớp dữ liệu, làm schema khó bảo trì và khó scale.

Trong môi trường thực tế, hệ thống job crawl thường phải xử lý các tình huống như một job đổi URL, nguồn cập nhật lại nội dung, source không có job ID ổn định hoặc nhiều worker cùng lấy dữ liệu song song. Vì vậy schema cần được thiết kế theo hướng tách rõ trách nhiệm, tăng khả năng upsert và đảm bảo idempotency.

## 3. Nguyên tắc thiết kế

Thiết kế đề xuất dựa trên các nguyên tắc sau:

- Tách dữ liệu vận hành crawl khỏi dữ liệu nghiệp vụ job.
- Tách dữ liệu raw khỏi dữ liệu chuẩn hóa.
- Mọi bản ghi job cần có cơ chế nhận diện duy nhất theo nguồn.
- Dedupe không chỉ dựa vào URL mà phải có thêm fingerprint nội dung.
- Trạng thái AI, embedding, indexing không nên để chung trong bảng job chính.
- Queue phải đủ dữ liệu để worker xử lý an toàn, retry được và tránh xử lý trùng.
- Hệ thống phải hỗ trợ re-crawl và re-parse mà không làm mất lịch sử quan trọng.

## 4. Kiến trúc dữ liệu tổng thể

Mô hình đề xuất gồm bảy bảng chính:

1. `sources`: danh mục các nguồn crawl.
2. `crawl_runs`: nhật ký từng đợt crawl.
3. `crawl_queue`: hàng đợi task crawl hoặc refresh.
4. `raw_jobs`: dữ liệu raw lấy trực tiếp từ source.
5. `jobs`: dữ liệu job chuẩn hóa để dùng cho search, filter, AI.
6. `job_locations`: dữ liệu location được tách riêng để dễ mở rộng.
7. `job_processing`: trạng thái xử lý hậu kỳ như AI, embedding, indexing.

Cách tách bảng như trên giúp hệ thống xử lý tốt cả ba nhu cầu: vận hành crawler, lưu bằng chứng nguồn gốc dữ liệu và phục vụ layer sản phẩm.

## 5. Mô tả chi tiết từng bảng

### 5.1. Bảng `sources`

Bảng này lưu thông tin cấu hình logic của từng nguồn tuyển dụng như ITViec, TopCV, VietnamWorks hoặc LinkedIn. Đây là lớp master data để liên kết toàn bộ các bảng còn lại.

#### Vai trò

- Định danh nguồn crawl.
- Quản lý trạng thái active hoặc inactive của source.
- Là khóa liên kết cho `crawl_runs`, `crawl_queue`, `raw_jobs` và `jobs`.

#### Cấu trúc đề xuất

```sql
CREATE TABLE sources (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    base_url TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

#### Ghi chú thực tế

- `code` nên là định danh kỹ thuật ổn định, ví dụ `itviec`, `topcv`, `vietnamworks`.
- Không nên dùng `name` cho logic hệ thống vì tên hiển thị có thể đổi.
- Có thể mở rộng thêm cột `rate_limit_per_minute`, `crawler_type`, `config_json` nếu sau này cần.

### 5.2. Bảng `crawl_runs`

Bảng này lưu metadata cho từng lần chạy crawl. Đây là bảng rất quan trọng để audit, monitor và đối soát sản lượng dữ liệu crawl theo thời gian.

#### Vai trò

- Theo dõi một lần crawl bắt đầu khi nào, kết thúc khi nào.
- Biết source nào đang crawl, crawl full hay incremental.
- Tổng hợp số lượng page và job đã xử lý.
- Hỗ trợ debugging khi có lỗi hệ thống.

#### Cấu trúc đề xuất

```sql
CREATE TABLE crawl_runs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    run_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    pages_crawled INT DEFAULT 0,
    jobs_seen INT DEFAULT 0,
    jobs_new INT DEFAULT 0,
    jobs_updated INT DEFAULT 0,
    error_message TEXT
);
```

#### Ghi chú thực tế

- `run_type` có thể là `full`, `incremental`, `backfill`, `repair`.
- `status` có thể là `running`, `success`, `partial_success`, `failed`.
- Bảng này giúp xác định một issue đến từ parser, source hay scheduler.

### 5.3. Bảng `crawl_queue`

Đây là bảng queue mức database. Nếu hệ thống chưa dùng RabbitMQ, Kafka hoặc SQS thì database queue là lựa chọn thực tế và đủ ổn cho nhiều hệ thống vừa và nhỏ.

#### Vai trò

- Chứa các task chờ crawl hoặc refresh.
- Điều phối worker xử lý song song.
- Hỗ trợ retry, priority, scheduling và lock.
- Đảm bảo một task không bị enqueue trùng nhiều lần.

#### Cấu trúc đề xuất

```sql
CREATE TABLE crawl_queue (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    run_id BIGINT REFERENCES crawl_runs(id),
    task_type VARCHAR(30) NOT NULL,
    task_key VARCHAR(255) NOT NULL,
    payload JSONB,
    priority SMALLINT NOT NULL DEFAULT 5,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    locked_by VARCHAR(100),
    locked_at TIMESTAMP,
    scheduled_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, task_type, task_key)
);
```

#### Ghi chú thực tế

- `task_type` nên phân biệt rõ như `list_page`, `detail_page`, `refresh_job`, `reparse_job`.
- `task_key` là khóa chống enqueue trùng; ví dụ có thể là URL đã normalize hoặc source job key.
- `processed` kiểu boolean không đủ dùng trong production; `status` là mô hình đúng hơn.
- `locked_by` và `locked_at` giúp xử lý cơ chế lease lock cho worker.
- `scheduled_at` cho phép delay retry hoặc crawl theo lịch.

### 5.4. Bảng `raw_jobs`

Đây là bảng lưu bản ghi raw đúng như dữ liệu source trả về hoặc parser tạo ra sau bước extract ban đầu. Bảng này rất quan trọng trong thực tế vì giúp reprocess mà không cần crawl lại.

#### Vai trò

- Lưu dữ liệu gốc để kiểm chứng và debug.
- Giúp parse lại khi business rule thay đổi.
- Hỗ trợ đối chiếu giữa dữ liệu raw và dữ liệu canonical.
- Là lớp lưu bằng chứng nguồn gốc dữ liệu.

#### Cấu trúc đề xuất

```sql
CREATE TABLE raw_jobs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    run_id BIGINT REFERENCES crawl_runs(id),
    source_job_key VARCHAR(255),
    source_url TEXT,
    canonical_url TEXT,
    payload JSONB NOT NULL,
    payload_hash CHAR(64),
    fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_deleted_at_source BOOLEAN DEFAULT FALSE,
    UNIQUE (source_id, source_job_key),
    UNIQUE (source_id, canonical_url)
);
```

#### Ghi chú thực tế

- `payload` nên chứa toàn bộ JSON hoặc object parser, không chỉ vài field đã chọn.
- `payload_hash` giúp phát hiện source có cập nhật nội dung hay không.
- Nếu source xóa job, có thể update `is_deleted_at_source = true` thay vì xóa record.
- Nếu một số source không có `source_job_key`, vẫn có thể dedupe bằng `canonical_url`.

### 5.5. Bảng `jobs`

Đây là bảng canonical và là bảng quan trọng nhất cho nghiệp vụ. Tất cả dữ liệu ở bảng này phải là dữ liệu đã được chuẩn hóa để phục vụ filter, search, ranking, recommendation và AI enrichment.

#### Vai trò

- Lưu phiên bản job chuẩn hóa cho ứng dụng sử dụng.
- Tách biệt với dữ liệu raw.
- Hỗ trợ query nhanh theo title, company, salary, location, ngày đăng.
- Là lớp dùng để dedupe job theo logic nghiệp vụ.

#### Cấu trúc đề xuất

```sql
CREATE TABLE jobs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    raw_job_id BIGINT REFERENCES raw_jobs(id),
    source_job_key VARCHAR(255),
    source_url TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    title_normalized TEXT,
    company_name TEXT NOT NULL,
    company_normalized TEXT,
    employment_type VARCHAR(50),
    category VARCHAR(100),
    skills_text TEXT,
    salary_min NUMERIC(14,2),
    salary_max NUMERIC(14,2),
    salary_currency VARCHAR(10),
    salary_period VARCHAR(20),
    salary_raw TEXT,
    posted_at DATE,
    expires_at DATE,
    description TEXT,
    description_hash CHAR(64),
    content_fingerprint CHAR(64) NOT NULL,
    job_status VARCHAR(20) NOT NULL DEFAULT 'active',
    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_crawled_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, source_job_key),
    UNIQUE (source_id, canonical_url),
    UNIQUE (source_id, content_fingerprint)
);
```

#### Ghi chú thực tế

- `title_normalized` và `company_normalized` phục vụ dedupe và search.
- `skills_text` đủ dùng cho giai đoạn đầu; nếu sau này cần query kỹ năng mạnh hơn thì tách thành `skills` và `job_skills`.
- `salary_raw` nên giữ lại để không mất dữ liệu gốc, nhưng filter chính phải dựa vào `salary_min`, `salary_max`, `salary_currency`, `salary_period`.
- `first_seen_at` và `last_seen_at` rất quan trọng để phân biệt job mới với job chỉ được crawl lại.
- `job_status` không nên chỉ phản ánh trạng thái crawl mà nên phản ánh tình trạng nghiệp vụ như `active`, `expired`, `closed`, `hidden`.

### 5.6. Bảng `job_locations`

Dữ liệu location thường rất bẩn và có thể thay đổi cấu trúc tùy source. Việc tách location ra khỏi `jobs` giúp schema linh hoạt hơn, đặc biệt nếu sau này một job có nhiều địa điểm hoặc hỗ trợ remote/hybrid.

#### Vai trò

- Chuẩn hóa thông tin địa điểm.
- Hỗ trợ mở rộng một job có nhiều location.
- Giảm mức độ phụ thuộc của bảng `jobs` vào dữ liệu text khó chuẩn hóa.

#### Cấu trúc đề xuất

```sql
CREATE TABLE job_locations (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    country_code VARCHAR(10) DEFAULT 'VN',
    city TEXT,
    district TEXT,
    street TEXT,
    full_address TEXT,
    is_remote BOOLEAN DEFAULT FALSE
);
```

#### Ghi chú thực tế

- Với dữ liệu hiện tại, các field như `location_city`, `location_district`, `location_street` map rất tự nhiên vào bảng này.
- Nếu sau này muốn phân tích theo địa lý sâu hơn, có thể thêm `ward`, `lat`, `lng`, `region_code`.
- Nếu một job hoàn toàn remote, có thể set `is_remote = true` và để trống các cột địa chỉ.

### 5.7. Bảng `job_processing`

Không nên để trạng thái AI hoặc indexing trong bảng `jobs`, vì đó là trạng thái pipeline kỹ thuật chứ không phải dữ liệu nghiệp vụ chính. Việc tách bảng này làm kiến trúc rõ ràng hơn.

#### Vai trò

- Theo dõi các bước xử lý hậu kỳ.
- Hỗ trợ job AI enrichment, embedding, classification, indexing.
- Tránh làm bảng `jobs` trở nên cồng kềnh.

#### Cấu trúc đề xuất

```sql
CREATE TABLE job_processing (
    job_id BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    ai_processed BOOLEAN NOT NULL DEFAULT FALSE,
    ai_processed_at TIMESTAMP,
    parsing_status VARCHAR(20) DEFAULT 'parsed',
    embedding_status VARCHAR(20) DEFAULT 'pending',
    indexing_status VARCHAR(20) DEFAULT 'pending',
    quality_score NUMERIC(5,2),
    tier VARCHAR(20),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

#### Ghi chú thực tế

- `quality_score` có thể dùng để chấm độ đầy đủ hoặc độ tin cậy của dữ liệu.
- `tier` chỉ nên là metadata phục vụ ranking hoặc business rule, không nên nằm ở `crawl_state` như trước.
- Nếu sau này có nhiều pipeline khác nhau, có thể tách tiếp thành bảng event hoặc job pipeline history.

## 6. Quan hệ giữa các bảng

Quan hệ đề xuất như sau:

- Một `source` có nhiều `crawl_runs`.
- Một `source` có nhiều `crawl_queue` items.
- Một `crawl_run` có nhiều task trong `crawl_queue`.
- Một `crawl_run` có nhiều `raw_jobs`.
- Một `raw_job` có thể map đến một `job` canonical.
- Một `job` có thể có một hoặc nhiều `job_locations`.
- Một `job` có một record `job_processing`.

Cách tổ chức này cho phép trace ngược từ job canonical về raw payload và về đúng đợt crawl đã tạo ra nó.

## 7. Chiến lược chống crawl trùng và dedupe job

Đây là phần quan trọng nhất của thiết kế.

### 7.1. Mục tiêu

Hệ thống cần tránh ba loại trùng lặp:

- Cùng một task bị enqueue nhiều lần.
- Cùng một job từ cùng một source bị insert nhiều record.
- Cùng một job đổi URL hoặc chỉnh sửa nhẹ nhưng vẫn bị nhận thành job mới.

### 7.2. Dedupe nhiều lớp

Để xử lý thực tế, dedupe nên theo ba lớp:

#### Lớp 1: Task-level dedupe trong queue

Sử dụng unique key:

```sql
UNIQUE (source_id, task_type, task_key)
```

Điều này ngăn việc cùng một URL detail page hoặc cùng một source job key bị đẩy vào queue nhiều lần.

#### Lớp 2: Source-level dedupe trong `raw_jobs`

Ưu tiên dùng:

- `source_job_key` nếu source có ID ổn định.
- `canonical_url` nếu không có source job key.

Hai unique constraint này giúp tránh tạo nhiều bản ghi raw cho cùng một job từ cùng một nguồn.

#### Lớp 3: Business-level dedupe trong `jobs`

Dùng `content_fingerprint` để bắt các trường hợp khó hơn, ví dụ:

- Source đổi URL của job.
- Cùng job xuất hiện ở nhiều endpoint khác nhau trong cùng source.
- Job được cập nhật một số text không làm thay đổi bản chất bản tin.

### 7.3. Công thức fingerprint khuyến nghị

`content_fingerprint` nên được tạo từ chuỗi normalize như sau:

```text
source_code + normalized(title) + normalized(company_name) + normalized(location_city) + normalized(location_district) + posted_at
```

Sau đó hash bằng SHA-256.

#### Nguyên tắc normalize

- Chuyển về lowercase.
- Trim khoảng trắng thừa.
- Loại bỏ ký tự đặc biệt không cần thiết.
- Chuẩn hóa unicode và dấu câu.
- Map các biến thể phổ biến của tên công ty hoặc title nếu cần.

### 7.4. Thứ tự upsert khuyến nghị

Khi insert hoặc update job, thứ tự kiểm tra nên là:

1. `source_job_key`.
2. `canonical_url`.
3. `content_fingerprint`.

Nếu đã tồn tại record tương ứng thì không tạo job mới. Thay vào đó:

- Update `last_seen_at`.
- Update `last_crawled_at`.
- Update các field thay đổi nếu cần.
- Giữ nguyên `first_seen_at`.

### 7.5. Vì sao không chỉ dùng URL

Trong thực tế, URL không phải lúc nào cũng ổn định. Nhiều trang tuyển dụng thay đổi slug theo title, campaign hoặc tracking parameter. Nếu dedupe chỉ dựa vào URL thì rất dễ sinh trùng record cho cùng một job. Vì vậy fingerprint là lớp bảo vệ cần thiết trong production.

## 8. Những trường dữ liệu nên bỏ hoặc tách khỏi thiết kế cũ

Thiết kế cũ có một số field nên bỏ hoặc dời sang bảng khác:

### Nên bỏ khỏi `crawl_state`

- `title`
- `company`
- `description`
- `skills`
- `category`
- `tier`
- `ai_processed`

Lý do là vì các field này không thuộc cùng một trách nhiệm. Thông tin nội dung job nên ở `jobs`; trạng thái xử lý AI nên ở `job_processing`.

### Nên thay thế trong `crawl_queue`

- `processed BOOLEAN` nên đổi thành `status`, `attempts`, `processed_at`, `error_message`.

### Nên giữ nhưng đổi vai trò

- `salary_raw`: vẫn giữ để bảo toàn dữ liệu gốc, nhưng không dùng làm field chính cho filter.
- `location_street`: giữ ở `job_locations`, không nên nhét vào bảng `jobs` chính.
- `description`: giữ ở `jobs`, đồng thời raw đầy đủ phải nằm trong `raw_jobs.payload`.

## 9. Chỉ mục khuyến nghị

Để đảm bảo hiệu năng, nên tạo các index sau:

```sql
CREATE INDEX idx_crawl_queue_status_scheduled
ON crawl_queue (status, scheduled_at, priority);

CREATE INDEX idx_raw_jobs_source_fetched
ON raw_jobs (source_id, fetched_at DESC);

CREATE INDEX idx_jobs_status_last_seen
ON jobs (job_status, last_seen_at DESC);

CREATE INDEX idx_jobs_posted_at
ON jobs (posted_at DESC);

CREATE INDEX idx_jobs_company_normalized
ON jobs (company_normalized);

CREATE INDEX idx_jobs_title_normalized
ON jobs (title_normalized);
```

### Gợi ý mở rộng index

Tùy nhu cầu sản phẩm, có thể bổ sung:

- Index cho `employment_type` nếu filter loại hình việc làm nhiều.
- GIN index trên `payload` hoặc `skills_text` nếu dùng PostgreSQL full text search.
- Partial index cho `job_status = 'active'` nếu đa số truy vấn chỉ lấy job active.

## 10. Luồng dữ liệu vận hành đề xuất

### Bước 1: Tạo crawl run

Scheduler hoặc operator tạo một record trong `crawl_runs` để đánh dấu bắt đầu một phiên crawl.

### Bước 2: Enqueue task

Hệ thống tạo các task vào `crawl_queue`, ví dụ crawl danh sách page hoặc crawl chi tiết job. Mỗi task phải có `task_key` duy nhất trong phạm vi `source_id` và `task_type`.

### Bước 3: Worker lấy task

Worker lấy các task có `status = 'queued'`, set `locked_by`, `locked_at` và chuyển sang `processing`.

### Bước 4: Crawl dữ liệu raw

Sau khi lấy được nội dung từ source, worker lưu raw vào `raw_jobs`. Nếu raw đã tồn tại theo unique key thì có thể update `payload`, `payload_hash`, `fetched_at`.

### Bước 5: Parse và normalize

Worker hoặc một pipeline riêng parse `payload` để tạo dữ liệu chuẩn hóa và upsert vào `jobs`.

### Bước 6: Tạo hoặc update location và processing state

Dữ liệu location được ghi vào `job_locations`, còn trạng thái enrich hoặc AI được ghi vào `job_processing`.

### Bước 7: Đánh dấu hoàn tất task

Task trong `crawl_queue` được update sang `done`, `failed` hoặc `skipped` tùy kết quả.

### Bước 8: Kết thúc crawl run

Khi toàn bộ task xong, hệ thống update `crawl_runs.status`, `finished_at` và các counter tổng hợp.

## 11. Khuyến nghị triển khai thực tế

### 11.1. Chọn PostgreSQL

PostgreSQL rất phù hợp cho use case này vì hỗ trợ tốt:

- `JSONB` cho raw payload.
- Unique constraint và upsert mạnh.
- Full text search hoặc GIN index khi cần.
- Khả năng mở rộng tốt cho workload crawl tầm vừa.

### 11.2. Dùng transaction cho upsert quan trọng

Các bước sau nên nằm trong transaction nếu xử lý đồng bộ:

- Insert hoặc update `raw_jobs`.
- Upsert `jobs`.
- Update `job_locations`.
- Update `job_processing`.
- Mark queue status.

Điều này tránh tình trạng raw đã có nhưng job canonical chưa tạo xong hoặc queue đã báo done nhưng dữ liệu chưa complete.

### 11.3. Soft delete thay vì hard delete

Nếu source xóa job, không nên xóa record khỏi `jobs`. Thực tế nên chuyển `job_status` sang `expired`, `closed` hoặc `hidden`, đồng thời update `last_seen_at` để giữ lịch sử dữ liệu.

### 11.4. Chuẩn bị cho reprocess

Một trong những nhu cầu rất phổ biến là thay parser hoặc thêm AI enrichment sau khi đã crawl hàng trăm nghìn job. Vì vậy raw payload phải được giữ đủ lâu và có thể reparse mà không phụ thuộc lại vào source.

### 11.5. Tách skill table khi quy mô đủ lớn

Ở giai đoạn đầu, `skills_text` trong bảng `jobs` là đủ. Khi sản phẩm cần matching sâu giữa candidate và job, nên tách thêm:

- `skills`
- `job_skills`

Điều này giúp chuẩn hóa kỹ năng, loại bỏ synonym và hỗ trợ analytics tốt hơn.

## 12. Rủi ro và cách giảm thiểu

| Rủi ro | Mô tả | Cách giảm thiểu |
|---|---|---|
| URL thay đổi | Cùng một job có URL khác nhau | Dùng `content_fingerprint` ngoài `canonical_url` |
| Source không có ID ổn định | Khó dedupe theo khóa nguồn | Normalize URL và tạo fingerprint từ title, company, location, posted_at |
| Worker xử lý trùng task | Nhiều worker lấy cùng một task | Dùng `status`, `locked_by`, `locked_at`, transaction khi claim task |
| Parser thay đổi | Dữ liệu cũ cần chuẩn hóa lại | Giữ `raw_jobs.payload` để reprocess |
| Query chậm | Tăng dữ liệu theo thời gian | Thêm index đúng nhu cầu, partition nếu dữ liệu rất lớn |
| Bảng `jobs` phình to | Nhét quá nhiều field kỹ thuật | Tách `job_processing`, `job_locations`, raw layer |

## 13. Kết luận và đề xuất áp dụng

Schema đề xuất phù hợp hơn cho môi trường thực tế vì tách bạch được ba lớp quan trọng: lớp vận hành crawl, lớp dữ liệu raw và lớp dữ liệu canonical. Cách tổ chức này giúp hệ thống dễ bảo trì, dễ mở rộng sang nhiều nguồn, dễ debug khi parser lỗi và đủ linh hoạt để thêm pipeline AI về sau.

Điểm quan trọng nhất là cơ chế chống trùng không chỉ nằm ở queue mà phải xuất hiện ở cả raw layer và canonical layer. Khi áp dụng đầy đủ `task_key`, `source_job_key`, `canonical_url` và `content_fingerprint`, hệ thống có thể giảm đáng kể nguy cơ crawl trùng, insert trùng hoặc tạo nhiều record cho cùng một job.

Trong giai đoạn triển khai, nên ưu tiên hoàn thiện trước các thành phần cốt lõi gồm `sources`, `crawl_runs`, `crawl_queue`, `raw_jobs` và `jobs`. Sau đó bổ sung `job_locations` và `job_processing` để làm sạch thiết kế và hỗ trợ pipeline nâng cao.