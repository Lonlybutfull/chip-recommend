#!/usr/bin/env python3
"""Classify non-official field_provenance sources → is_official flag.

Usage:
    python scripts/classify_official_sources.py          # dry-run preview
    python scripts/classify_official_sources.py --write  # apply to DB
"""
import sys, os, re, sqlite3, argparse
from urllib.parse import urlparse

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
DB_PATH = os.path.join(_project_root, "data", "data.db")

# ── Heuristic rules for "is this an official/trusted source?" ──
# Each rule is (domain_pattern, is_official, label)
# Higher-priority rules first.
DOMAIN_RULES = [
    # ── Exclude search engine result pages (before domain matching) ──
    ("/search?", "0", "搜索引擎结果页"),
    ("/search/?", "0", "搜索引擎结果页"),
    # ── Definitely official: vendor websites ──
    ("nvidia.com", "1", "NVIDIA 官方"),
    ("amd.com", "1", "AMD 官方"),
    ("intel.com", "1", "Intel 官方"),
    ("intel.cn", "1", "Intel 官方"),
    ("google.com", "1", "Google 官方"),
    ("cloud.google.com", "1", "Google Cloud 官方"),
    ("hiascend.com", "1", "昇腾官方"),
    ("huawei.com", "1", "华为官方"),
    ("cambricon.com", "1", "寒武纪官方"),
    ("enflame-tech.com", "1", "燧原官方"),
    ("iluvatar.com", "1", "天数智芯官方"),
    ("birentech.com", "1", "壁仞官方"),
    ("metax-tech.com", "1", "沐曦官方"),
    ("kunlunxin.com", "1", "昆仑芯官方"),
    ("zhaoxin.com", "1", "兆芯官方"),
    ("horizon.ai", "1", "地平线官方"),
    ("cerebras.net", "1", "Cerebras 官方"),
    ("cerebras.ai", "1", "Cerebras 官方"),
    ("groq.com", "1", "Groq 官方"),
    ("sambanova.ai", "1", "SambaNova 官方"),
    ("graphcore.ai", "1", "Graphcore 官方"),
    ("tenstorrent.com", "1", "Tenstorrent 官方"),
    ("apple.com", "1", "Apple 官方"),
    ("qualcomm.com", "1", "Qualcomm 官方"),
    ("mediatek.com", "1", "MediaTek 官方"),
    ("arm.com", "1", "ARM 官方"),
    ("tsmc.com", "1", "TSMC 官方"),
    ("samsung.com", "1", "Samsung 官方"),
    ("micron.com", "1", "Micron 官方"),
    ("skhynix.com", "1", "SK Hynix 官方"),
    ("broadcom.com", "1", "Broadcom 官方"),
    ("marvell.com", "1", "Marvell 官方"),
    ("xilinx.com", "1", "Xilinx/AMD 官方"),
    ("habana.ai", "1", "Habana/Intel 官方"),
    ("aws.amazon.com", "1", "AWS 官方"),
    ("azure.microsoft.com", "1", "Azure 官方"),
    ("baidu.com/search", "0", "百度搜索结果"),
    ("baidu.com", "1", "百度官方"),
    ("developer.baidu.com", "1", "百度开发者官方"),
    ("aliyun.com", "1", "阿里云官方"),
    ("huggingface.co", "1", "HuggingFace 官方"),
    # ── Semi-official / widely trusted ──
    ("wikipedia.org", "1", "Wikipedia"),
    ("wikichip.org", "1", "WikiChip"),
    ("techpowerup.com", "1", "TechPowerUp GPU DB"),
    ("anandtech.com", "1", "AnandTech"),
    ("tomshardware.com", "1", "Tom's Hardware"),
    ("servethehome.com", "1", "ServeTheHome"),
    ("mlcommons.org", "1", "MLPerf/MLCommons 官方"),
    ("arxiv.org", "1", "arXiv"),
    ("baike.baidu.com", "1", "百度百科"),
    ("github.com", "1", "GitHub (可信社区)"),
    ("docs.vllm.ai", "1", "vLLM 官方文档"),
    ("github.com/vllm-project", "1", "vLLM Project"),
    ("docs.nvidia.com", "1", "NVIDIA 官方文档"),
    ("cdna.AMD.com", "1", "AMD 官方文档"),
    # ── Well-known tech news (medium trust, 官方 → this term means "the spec info is official", but the news site is NOT official, so it's 0) ──
    ("cnblogs.com", "0", "博客园"),
    ("ithome.com", "0", "IT之家"),
    ("csdn.net", "0", "CSDN"),
    ("zhidx.com", "0", "智东西"),
    ("leiphone.com", "0", "雷锋网"),
    ("ofweek.com", "0", "OFweek"),
    ("iotdt.com", "0", "物联网智库"),
    ("eepw.com.cn", "0", "EEPW"),
    ("icsmart.cn", "0", "ICSmarT"),
    ("guru3d.com", "0", "Guru3D"),
    ("wccftech.com", "0", "WCCFTech"),
    ("techspot.com", "0", "TechSpot"),
    ("storagereview.com", "0", "StorageReview"),
    ("techweb.com.cn", "0", "TechWeb"),
    ("exmoo.com", "0", "Exmoo"),
    ("c114.net.cn", "0", "C114"),
    ("smzdm.com", "0", "什么值得买"),
    ("dfrobot.com.cn", "0", "DFRobot"),
    ("xcc.com", "0", "小陈陈"),
    ("jemoic.com", "0", "杰美康"),
    ("gitee.com", "0", "Gitee"),
    ("zhihu.com", "0", "知乎"),
    ("donanimhaber.com", "0", "DonanimHaber"),
    ("zol.com.cn", "0", "中关村在线"),
    ("mydrivers.com", "0", "快科技"),
    ("google.com/search", "0", "Google 搜索结果页"),
    ("/search?q=", "0", "搜索引擎结果页"),
    # ── Script/internal sources ──
    ("dedup_chips.py", "0", "内部去重脚本"),
    ("dedup_manual", "0", "手动去重"),
]

# ── Patterns that override: if source_url contains these, force is_official ──
FORCE_OFFICIAL_PATTERNS = [
    r'/official[_-]?docs?/',
    r'/datasheet[s]?/',
    r'/product[_-]?brief/',
    r'/spec[s]?[_-]?sheet/',
    r'\.pdf$',
    r'datasheet',
    r'product-brief',
    r'/products/',
    r'/specifications/',
]

# ── Patterns that suggest the content itself is authoritative ──
FORCE_UNOFFICIAL_PATTERNS = [
    r'blog\.',
    r'/blog/',
    r'/news/',
    r'/article/',
    r'forum\.',
    r'/forum/',
    r'/t/',            # reddit-style
    r'/r/',            # reddit
    r'\.csdn\.',
    r'\.cnblogs\.',
    r'\.zhihu\.',
    r'\.ithome\.',
]


def classify_url(source_url: str) -> tuple[str, str]:
    """Return (is_official, reason_label)."""
    if not source_url or source_url.strip() == "":
        return "0", "空来源"

    url = source_url.strip()

    # Check force-official patterns first
    for pat in FORCE_OFFICIAL_PATTERNS:
        if re.search(pat, url, re.I):
            # Check domain rules to see if it's from a trusted domain; if not, just skip
            pass

    # Check domain rules (exact first, then substring)
    url_lower = url.lower()
    for domain, flag, label in DOMAIN_RULES:
        if domain in url_lower:
            return flag, label

    # Check force-unofficial patterns
    for pat in FORCE_UNOFFICIAL_PATTERNS:
        if re.search(pat, url, re.I):
            return "0", f"非官方 ({pat})"

    # Try to parse domain
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        host = ""

    # Heuristic: if hostname looks like a vendor domain (no news/blog words)
    if host and not any(kw in host for kw in ['news', 'blog', 'forum', 'bbs', 'post']):
        return "0", f"未识别 ({host})"

    return "0", "未识别"


def main():
    ap = argparse.ArgumentParser(description="Classify non-official provenance sources")
    ap.add_argument("--write", action="store_true", help="Apply changes to DB")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows to process")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get all rows with is_official = '0'
    rows = conn.execute("""
        SELECT id, source_url, source_type, row_id as chip_row_id
        FROM field_provenance
        WHERE is_official = '0'
        ORDER BY id
        """ + (f"LIMIT {args.limit}" if args.limit else "")
    ).fetchall()

    print(f"Found {len(rows)} non-official provenance rows")
    print()

    # Classify
    changes = []
    stats = {"官方(0→1)": 0, "保持非官方": 0, "URL规则命中": {}}
    for row in rows:
        flag, label = classify_url(row['source_url'] or '')
        if flag != '0':  # Only track rows that change
            changes.append((row['id'], flag, label))
            if flag == '1':
                stats["官方(0→1)"] += 1
            else:
                stats["保持非官方"] += 1
            stats["URL规则命中"][label] = stats["URL规则命中"].get(label, 0) + 1

    upgraded = sum(1 for _, flag, _ in changes if flag == '1')
    print(f"可升级为官方的: {upgraded}")
    print(f"保持非官方的: {len(changes) - upgraded}")
    print()
    print("=== 规则命中分布 ===")
    for label, cnt in sorted(stats["URL规则命中"].items(), key=lambda x: -x[1]):
        print(f"  {label}: {cnt}")

    # Show sample of each category
    print()
    print("=== 将升为官方的示例 (前20) ===")
    for cid, flag, label in [c for c in changes if c[1] == '1'][:20]:
        row = conn.execute("SELECT source_url, field_name FROM field_provenance WHERE id=?", (cid,)).fetchone()
        print(f"  [{cid}] {label:25s} | {row['field_name']:25s} | {(row['source_url'] or '')[:80]}")

    if args.write:
        count = 0
        for cid, flag, label in changes:
            conn.execute("UPDATE field_provenance SET is_official = ? WHERE id = ?", (flag, cid))
            count += 1
        conn.commit()
        print(f"\n[OK] Updated {count} records")
        # Re-count
        total = conn.execute("SELECT COUNT(*) FROM field_provenance").fetchone()[0]
        off = conn.execute("SELECT COUNT(*) FROM field_provenance WHERE is_official='1'").fetchone()[0]
        unoff = conn.execute("SELECT COUNT(*) FROM field_provenance WHERE is_official='0'").fetchone()[0]
        print(f"  Total: {total}, Official: {off} ({off*100//total}%), Non-official: {unoff} ({unoff*100//total}%)")
    else:
        print(f"\n(dry run, add --write to commit changes)")

    conn.close()


if __name__ == "__main__":
    main()
