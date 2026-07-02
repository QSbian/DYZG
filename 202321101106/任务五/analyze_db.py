# -*- coding: utf-8 -*-
"""OMS Sales Database 综合分析脚本"""
import sqlite3
import sys
import io

# 确保控制台正确输出中文
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = r'C:\Users\25722\WorkBuddy\2026-07-02-09-17-51\oms_sales_data.sqlite'

def analyze():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("=" * 70)
    print("OMS 销售数据库 - 初步分析汇报")
    print("=" * 70)
    print()

    # ==================== 1. 数据库概览 ====================
    print("【一、数据库概览】")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"  包含 {len(tables)} 张表/视图: {', '.join(tables)}")
    print()

    # 文件大小
    import os
    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"  数据库文件大小: {size_mb:.1f} MB")
    print()

    # ==================== 2. 主表分析 ====================
    print("【二、adb_model_sales_summary（销售汇总主表）】")
    print()

    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary")
    total_rows = cursor.fetchone()[0]
    print(f"  总记录数: {total_rows:,}")

    # 时间维度
    cursor.execute("SELECT MIN(fin_year_month), MAX(fin_year_month), COUNT(DISTINCT fin_year_month) FROM adb_model_sales_summary")
    min_ym, max_ym, months = cursor.fetchone()
    print(f"  时间范围: {min_ym} ~ {max_ym}（共 {months} 个月）")
    print(f"  数据跨度约 {months//12} 年")

    # 产品维度
    cursor.execute("SELECT COUNT(DISTINCT product_model), COUNT(DISTINCT product_category), COUNT(DISTINCT product_class) FROM adb_model_sales_summary")
    models, cats, classes = cursor.fetchone()
    print(f"  产品型号: {models} 个")
    print(f"  产品类别: {cats} 个")
    print(f"  产品细分类: {classes} 个")

    # 渠道维度
    cursor.execute("SELECT COUNT(DISTINCT channel), COUNT(DISTINCT channel_) FROM adb_model_sales_summary")
    main_ch, sub_ch = cursor.fetchone()
    print(f"  主渠道: {main_ch} 个")
    print(f"  子渠道: {sub_ch} 个")

    # 客户维度
    cursor.execute("SELECT COUNT(DISTINCT billto) FROM adb_model_sales_summary")
    customers = cursor.fetchone()[0]
    print(f"  客户: {customers} 个")

    # 销售统计
    cursor.execute("SELECT SUM(qty_total), AVG(qty_total), MAX(qty_total), MIN(qty_total), SUM(CASE WHEN qty_total < 0 THEN 1 ELSE 0 END) FROM adb_model_sales_summary WHERE deleted=0")
    total_qty, avg_qty, max_qty, min_qty, neg_count = cursor.fetchone()
    print(f"  总销量: {total_qty:,.0f} 件")
    print(f"  平均单记录销量: {avg_qty:.1f} 件")
    print(f"  最大单记录销量: {max_qty:,} 件")
    print(f"  最小单记录销量: {min_qty:,} 件")
    print(f"  负销量记录数: {neg_count} 条（可能为退货）")

    print()

    # ==================== 3. 产品类别分布 ====================
    print("【三、产品类别销量分布（TOP 15）】")
    cursor.execute("""
        SELECT product_category, COUNT(DISTINCT product_model), SUM(qty_total) as total_qty,
               ROUND(SUM(qty_total)*100.0/(SELECT SUM(qty_total) FROM adb_model_sales_summary WHERE deleted=0), 1) as pct
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY product_category ORDER BY total_qty DESC LIMIT 15
    """)
    print(f"  {'类别':<16} {'型号数':<8} {'销量':>12} {'占比':>8}")
    print(f"  {'-'*16} {'-'*8} {'-'*12} {'-'*8}")
    for row in cursor.fetchall():
        print(f"  {row[0]:<16} {row[1]:<8} {row[2]:>12,} {row[3]:>7.1f}%")
    print()

    # ==================== 4. 渠道分布 ====================
    print("【四、渠道销量分布】")
    cursor.execute("""
        SELECT channel, SUM(qty_total) as total_qty,
               ROUND(SUM(qty_total)*100.0/(SELECT SUM(qty_total) FROM adb_model_sales_summary WHERE deleted=0), 1) as pct
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY channel ORDER BY total_qty DESC
    """)
    print(f"  {'渠道':<20} {'销量':>12} {'占比':>8}")
    print(f"  {'-'*20} {'-'*12} {'-'*8}")
    for row in cursor.fetchall():
        print(f"  {row[0]:<20} {row[1]:>12,} {row[2]:>7.1f}%")
    print()

    # ==================== 5. 月度趋势 ====================
    print("【五、月度销量趋势（最近36个月）】")
    cursor.execute("""
        SELECT fin_year_month, SUM(qty_total) as total_qty
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY fin_year_month ORDER BY fin_year_month DESC LIMIT 36
    """)
    rows = cursor.fetchall()
    rows.reverse()
    print(f"  {'年月':<10} {'销量':>10} {'趋势'}")
    print(f"  {'-'*10} {'-'*10} {'-'*30}")
    max_q = max(r[1] for r in rows)
    for ym, qty in rows:
        bar_len = int(qty / max_q * 25)
        bar = "█" * bar_len
        print(f"  {ym:<10} {qty:>10,} {bar}")
    print()

    # ==================== 6. 年度汇总 ====================
    print("【六、年度销量汇总】")
    cursor.execute("""
        SELECT substr(fin_year_month, 1, 4) as year, SUM(qty_total) as total_qty
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY year ORDER BY year
    """)
    print(f"  {'年份':<8} {'总销量':>12} {'同比增长'}")
    print(f"  {'-'*8} {'-'*12} {'-'*12}")
    prev = None
    for row in cursor.fetchall():
        yoy = ""
        if prev:
            growth = (row[1] - prev) / prev * 100
            yoy = f"+{growth:.1f}%" if growth >= 0 else f"{growth:.1f}%"
        print(f"  {row[0]:<8} {row[1]:>12,} {yoy:>12}")
        prev = row[1]
    print()

    # ==================== 7. TOP 客户 ====================
    print("【七、TOP 15 客户（按销量）】")
    cursor.execute("""
        SELECT COALESCE(billto, '(NULL)'), SUM(qty_total) as total_qty,
               COUNT(DISTINCT product_model) as model_count
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY billto ORDER BY total_qty DESC LIMIT 15
    """)
    print(f"  {'客户代码':<20} {'销量':>10} {'型号数':>8}")
    print(f"  {'-'*20} {'-'*10} {'-'*8}")
    for row in cursor.fetchall():
        name = row[0] if row[0] else '(NULL)'
        print(f"  {str(name):<20} {row[1]:>10,} {(row[2] or 0):>8}")
    print()

    # ==================== 8. 季节性分析 ====================
    print("【八、月度季节性模式】")
    cursor.execute("""
        SELECT CAST(substr(fin_year_month, 5, 2) AS INTEGER) as month, 
               AVG(qty_total) * COUNT(DISTINCT fin_year_month) / COUNT(DISTINCT substr(fin_year_month,1,4)) as avg_monthly,
               SUM(qty_total) as total_qty
        FROM adb_model_sales_summary WHERE deleted=0 
        GROUP BY month ORDER BY month
    """)
    print(f"  {'月份':<8} {'月均销量':>12} {'总销量':>12} {'趋势'}")
    print(f"  {'-'*8} {'-'*12} {'-'*12} {'-'*30}")
    rows = cursor.fetchall()
    max_v = max(r[1] for r in rows)
    for m, avg, tot in rows:
        bar_len = int(tot / max(r[2] for r in rows) * 25)
        bar = "█" * bar_len
        print(f"  {m}月     {avg:>12,.0f} {tot:>12,} {bar}")
    print()

    # ==================== 9. 视图分析 ====================
    print("【九、vw_product_sales_customer_3（客户销售明细视图）】")
    cursor.execute("SELECT COUNT(*) FROM vw_product_sales_customer_3")
    view_rows = cursor.fetchone()[0]
    print(f"  总记录数: {view_rows:,}")
    
    # 获取列信息
    cursor.execute("PRAGMA table_info(vw_product_sales_customer_3)")
    cols = cursor.fetchall()
    print(f"  列数: {len(cols)}")
    col_names = [c[1] for c in cols]
    # 尝试正确编码
    try:
        decoded = [name.encode('latin-1').decode('gbk') for name in col_names]
        print(f"  列名: {', '.join(decoded)}")
    except:
        print(f"  列名(原始): {', '.join(col_names)}")
    
    # 样本数据
    cursor.execute("SELECT * FROM vw_product_sales_customer_3 LIMIT 5")
    print()
    print("  样本数据（前5行）:")
    for i, row in enumerate(cursor.fetchall()):
        print(f"    {i+1}. {list(row)}")
    print()

    # ==================== 10. 数据质量 ====================
    print("【十、数据质量概况】")
    
    # 负销量
    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary WHERE qty_total < 0 AND deleted=0")
    neg_qty = cursor.fetchone()[0]
    print(f"  负销量记录: {neg_qty} 条 ({neg_qty/total_rows*100:.2f}%) - 可能为退货/冲销")
    
    # 已删除记录
    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary WHERE deleted=1")
    deleted = cursor.fetchone()[0]
    print(f"  已删除记录: {deleted} 条 ({deleted/total_rows*100:.2f}%)")
    
    # 缺失值统计
    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary WHERE product_category IS NULL")
    null_cat = cursor.fetchone()[0]
    print(f"  产品类别为空: {null_cat} 条")
    
    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary WHERE product_model IS NULL")
    null_model = cursor.fetchone()[0]
    print(f"  产品型号为空: {null_model} 条")
    
    cursor.execute("SELECT COUNT(*) FROM adb_model_sales_summary WHERE channel IS NULL")
    null_ch = cursor.fetchone()[0]
    print(f"  渠道为空: {null_ch} 条")
    print()

    conn.close()
    print("=" * 70)
    print("分析完成")
    print("=" * 70)

if __name__ == '__main__':
    analyze()
