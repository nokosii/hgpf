# HGPF AI–RAG 臺灣客家族譜證據數位治理系統

這是一套可以本機執行的研究雛型，將客家族譜 OCR 文本轉成可追溯的證據段落，支援混合檢索、反證搜尋、主張—證據關聯、GPS 五項稽核，以及受證據約束的族譜書寫。

> 系統是 **GPS-aligned（GPS導向）**，不是 Board for Certification of Genealogists 的認證工具。稽核分數不能證明研究已「合理且詳盡」，也不能取代研究者解決衝突與核定結論。

## 快速啟動

在 PowerShell 執行：

```powershell
Set-Location -LiteralPath 'C:\族譜\hgpf-ai-rag-system'
.\run.ps1 -Seed
```

第一次執行會在專案內建立 `.venv` 並安裝依賴。看到啟動訊息後開啟：

```text
http://127.0.0.1:8765
```

若不想先載入示範族譜，改執行 `./run.ps1`。

## 已實作的研究流程

1. **S0 界定主張**：人物、親屬、遷徙、客家淵源、客語腔調、墓葬與風水均以可被支持或反駁的主張表示。
2. **S1 建檔與數位化**：匯入 MD、TXT、PDF、DOCX，保存原始路徑、SHA-256、版本 metadata、段落定位與存取層級。
   上傳介面會顯示目前檔名、位元組傳輸比例，以及索引／框架檢核的處理階段；完成後可立即查看「HGPF 31項候選覆蓋＋GPS五項研究就緒度預檢」與命中原文。
3. **S2 混合 RAG**：結合 SQLite 全文索引、中文雙字元雜湊向量、HGPF 欄位訊號、反證詞與OCR可用性排序；品質訊號只反映文字可讀性，不代表史料可信度。
4. **S3 證據稽核**：將段落標為支持、反駁、限制或脈絡，依 GPS 五項原則產生待辦；衝突處置必須具名。
5. **S4 受控書寫**：只使用已掛接的證據卡生成證明摘要，不補寫無來源細節，引用以〔E1〕等標籤對回文件與段落。
6. **S5 治理與發布**：草稿依 `Evidence-linked → Audit-flagged → Human-reviewed → Approved-for-publication / Needs-further-research` 管理。

## HGPF 操作化內容

- M1 描述層：原文、異體、稱謂、時間、地點與文化語境。
- M2 證據層：版本、頁欄、影像、轉錄、來源形成者與可重現引用。
- M3 推論／稽核層：支持、反駁、限制、實體解析、時序及版本衝突。
- M4 治理層：隱私、授權、存取、具名人工核定與修訂軌跡。
- 系統內建 2013 Hakka Genealogy Metadata 31個子項的在地化名稱、D1–D10 證據域及稽核焦點。

## 示範資料

`-Seed` 只讀取工作區現有的三份 OCR Markdown，不修改原檔：

- `所有電子檔/台灣_苗栗_張姓近代族譜_1冊(37頁)_1968.md`
- `所有電子檔/台灣_苗栗_徐氏大族譜_1冊(118頁)_1978-compressed.md`
- `所有電子檔/客09_詔安江氏志.md`

系統會建立一項「心展公祠位置與坐向」示範主張。只有同時含人物／祠名、地點及坐向錨點的片段才會成為候選支持證據；向量或全文相似度本身不會自動掛成支持。證據卡仍標示「待人工核對原頁」。

完整架構、資料模型、狀態機及HGPF需求對照見 [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md)。

## 資料與隱私

- SQLite 資料庫：`data/hgpf.db`
- 上傳檔：`data/uploads/`
- 預設只監聽 `127.0.0.1`，不對區域網路或網際網路公開。
- 公開前仍須進一步實作帳號、角色權限、欄位遮蔽、備份與稽核日誌；目前 `access_level` 是研究 metadata，不是強制存取控制。
- `processing_activities` 保存匯入、掛證、書寫與人工覆核的活動軌跡；原始文字不因OCR品質評估或草稿生成而被覆蓋。

## 執行測試

```powershell
Set-Location -LiteralPath 'C:\族譜\hgpf-ai-rag-system'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 主要 API

- `GET /api/framework`：HGPF四層、十證據域、31欄位及GPS五項。
- `POST /api/documents/import`：從本機路徑建檔。
- `POST /api/documents/upload`：上傳MD、TXT、PDF或DOCX並建立索引。
- `POST /api/documents/{id}/audit`：執行文件層級HGPF 31項與GPS五項預檢。
- `GET /api/documents/{id}/audits/latest`：取得文件最近一份預檢報告。
- `POST /api/search`：混合或反證導向檢索。
- `POST /api/claims`：建立可稽核主張。
- `POST /api/claims/{id}/evidence`：建立證據關係。
- `POST /api/audit/{id}`：執行GPS導向初檢。
- `POST /api/drafts`：建立受證據約束的證明摘要。
- `PATCH /api/drafts/{id}`：具名人工覆核與發布狀態。
- 互動式 API 文件：`http://127.0.0.1:8765/docs`

## 雛型邊界

- 字元向量是可重現的本機基線，不等同於大型語言模型 embedding；之後可替換成經評估的中文／古漢語 embedding。
- 系統不會自動把「書院」改成「書室」、不會由祖籍推定客語腔調，也不會把風水記載轉成客觀因果。
- PDF 若是純影像，需先 OCR；系統不在匯入時自動改寫 OCR 原文。
- GPS1 的合理詳盡性必須由研究者根據實體與數位館藏範圍判斷。
