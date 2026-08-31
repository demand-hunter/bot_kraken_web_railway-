from flask import Blueprint, render_template_string

downloads_page = Blueprint("downloads_page", __name__)

HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Turbo — Laboratório</title>
<style>
    body{
        margin:0;
        min-height:100vh;
        display:flex;
        align-items:center;
        justify-content:center;
        background:#0b1020;
        color:#eef3ff;
        font-family:Arial,Helvetica,sans-serif;
    }
    .box{
        width:min(92%,520px);
        padding:28px;
        background:#131a2a;
        border:1px solid #25304a;
        border-radius:16px;
        text-align:center;
    }
    h1{
        margin:0 0 8px;
        font-size:24px;
    }
    p{
        margin:0 0 22px;
        color:#9aa7c2;
    }
    a{
        display:block;
        margin:12px 0;
        padding:14px 18px;
        border-radius:10px;
        text-decoration:none;
        font-weight:700;
        background:#8fb5ff;
        color:#07111f;
    }
    .small{
        margin-top:18px;
        font-size:12px;
        color:#7f8ba6;
    }
</style>
</head>
<body>
    <div class="box">
        <h1>Trading Turbo — Laboratório</h1>
        <p>Download dos dados do experimento</p>

        <a href="/download/lab_history.csv">
            BAIXAR LAB HISTORY
        </a>

        <a href="/download/ranking_audit.csv">
            BAIXAR RANKING AUDIT
        </a>

        <div class="small">
            Esta página apenas acessa os arquivos já gerados pelo bot.
        </div>
    </div>
</body>
</html>
"""

@downloads_page.get("/downloads")
def downloads():
    return render_template_string(HTML)
