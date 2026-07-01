#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dir_statistic.py
统计指定目录下所有文件的字数（单词数）
- 中文：每个汉字算 1 字
- 英文：按空格/标点分隔的单词数
用法：python dir_statistic.py --dir=<目标目录>
"""

import os
import sys
import re
import argparse

# Windows 终端 UTF-8 输出修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def count_words(text: str) -> int:
    """
    统计文本的字数/单词数：
    - 中文字符（CJK 统一表意文字）：每个字符算 1
    - 英文及其他：按空白符分隔的 token 数
    """
    if not text:
        return 0

    # 匹配中文字符（CJK 统一表意文字基本区 + 扩展A/B）
    chinese_chars = re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002A6DF]', text)
    chinese_count = len(chinese_chars)

    # 去掉所有中文字符后，剩余文本按空白符分词统计英文单词数
    text_no_chinese = re.sub(
        r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002A6DF]', ' ', text
    )
    # 按空白符分割，过滤空字符串
    english_words = [w for w in text_no_chinese.split() if w.strip()]
    english_count = len(english_words)

    return chinese_count + english_count


def scan_directory(target_dir: str) -> dict:
    """
    递归扫描目录，返回 {相对路径: 字数} 的字典
    """
    result = {}
    target_dir = os.path.abspath(target_dir)

    for root, dirs, files in os.walk(target_dir):
        for fname in files:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, target_dir)

            # 尝试用常见编码读取文件
            content = None
            for encoding in ('utf-8', 'gbk', 'gb18030', 'latin-1'):
                try:
                    with open(full_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    break
                except (UnicodeDecodeError, PermissionError):
                    continue

            if content is None:
                # 二进制文件或无法读取，跳过
                continue

            word_count = count_words(content)
            result[rel_path] = word_count

    return result


def main():
    parser = argparse.ArgumentParser(description='统计指定目录下所有文件的字数/单词数')
    parser.add_argument('--dir', required=True, help='目标目录路径')
    args = parser.parse_args()

    target_dir = args.dir

    if not os.path.isdir(target_dir):
        print(f"错误：目录不存在 —— {target_dir}")
        sys.exit(1)

    file_stats = scan_directory(target_dir)

    total = sum(file_stats.values())

    # 按字数从多到少排序，字数相同按路径字母序
    sorted_files = sorted(file_stats.items(), key=lambda x: (-x[1], x[0]))

    print(f"总字数： {total}")
    print("-" * 30)
    for rel_path, count in sorted_files:
        # 统一用 / 显示路径，更清晰
        display_path = rel_path.replace(os.sep, '/')
        print(f"{display_path}:  {count}")


if __name__ == '__main__':
    main()
