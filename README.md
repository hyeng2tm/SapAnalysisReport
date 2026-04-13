# SAP Load Analyzer (AI-Powered)

Professional SAP S/4HANA performance analysis tool that transforms raw monitoring CSV data into executive-level PDF reports with AI-driven insights.

## 🚀 Key Features

- **Peak Window Analysis**: Automatically identifies contiguous high-load periods and maps them to resource-intensive SQL queries.
- **Transactional Lock Wait Deep Dive**: Dedicated analysis of top database lock contributors, identifying specific tables and programs causing contention.
- **AI Insights & RCA**: Integrated with Google Gemini 2.0 to provide Root Cause Analysis, priority-coded issues (Critical/High/Normal), and strategic recommendations.
- **Premium PDF Reporting**: "Open-Sided Modern" design featuring dual-axis charts, shaded peak windows, and professional typography.

## 📁 Repository Structure

- `main.py`: Main orchestration script for analysis and reporting.
- `core/processor.py`: Data cleaning, peak window identification, and SQL/Lock processing.
- `core/analyzer.py`: AI-powered performance analysis and RCA generation.
- `core/reporter.py`: High-quality PDF generation using `fpdf2`.
- `core/mailer.py`: Automated report distribution via email.

## 🛠 Setup & Usage

### 1. Prerequisites
- Python 3.12+
- Google Gemini API Key

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/hyeng2tm/SapAnalysisReport
cd SapAnalysisReport

# Setup virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the root directory:
```env
GOOGLE_API_KEY=your_gemini_api_key
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
```

### 4. Running the Analysis
Place your SAP monitoring CSV exports in the `data/` directory and run:
```bash
python main.py --data ./data --output ./reports
```

## 📊 Report Preview
The generated report includes:
1. **Executive Summary**: High-level system health dashboards.
2. **Peak Window SQL Analysis**: Top resource consumers during load spikes.
3. **Transactional Lock Wait Analysis**: Detailed lock contention mapping.
4. **Root Cause Analysis & Solutions**: Priority-coded (🔴/🟠/🟢) findings.
5. **AI Final Recommendation**: CPU load classification and short/mid-term action points.

---
Developed with **Antigravity AI SAP Observability Suite**.
