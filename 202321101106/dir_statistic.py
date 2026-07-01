#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dir_statistic.py - 统计指定目录下所有文件的字数（单词数）
中文按字统计，英文按单词统计
"""

import os
import re
import sys
import argparse

# Windows 下强制 stdout 使用 UTF-8，避免中文乱码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def count_words(text):
    """
    统计文本中的字数：
    - 中文字符：每个汉字计为1个字
    - 英文：按空格分割的单词计数
    - 数字：按连续数字段计为1个词
    """
    count = 0

    # 匹配中文字符（每个汉字计1）
    chinese_chars = re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text)
    count += len(chinese_chars)

    # 去掉中文字符后，统计英文单词和数字
    text_without_chinese = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', ' ', text)

    # 匹配英文单词和数字（连续字母或数字）
    english_words = re.findall(r'[a-zA-Z0-9]+(?:[\'_\-][a-zA-Z0-9]+)*', text_without_chinese)
    count += len(english_words)

    return count


def count_file_words(filepath):
    """读取文件并统计字数，尝试多种编码"""
    encodings = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1']
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                text = f.read()
            return count_words(text)
        except (UnicodeDecodeError, PermissionError):
            continue
    # 如果所有编码都失败，尝试二进制读取后解码
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        text = raw.decode('utf-8', errors='replace')
        return count_words(text)
    except Exception:
        return 0


def statistic_dir(target_dir):
    """遍历目录，统计每个文件的字数"""
    if not os.path.isdir(target_dir):
        print(f"错误：目录不存在或不是有效目录：{target_dir}")
        return

    file_counts = []  # [(相对路径, 字数), ...]
    total = 0

    for root, dirs, files in os.walk(target_dir):
        # 排序保证输出顺序稳定
        dirs.sort()
        for filename in sorted(files):
            filepath = os.path.join(root, filename)
            # 计算相对路径（相对于目标目录）
            rel_path = os.path.relpath(filepath, target_dir)
            # Windows 路径分隔符统一为正斜杠
            rel_path = rel_path.replace('\\', '/')

            word_count = count_file_words(filepath)
            file_counts.append((rel_path, word_count))
            total += word_count

    # 输出结果
    print(f"总字数： {total}")
    print("-------")
    for rel_path, wc in file_counts:
        print(f"{rel_path}:  {wc}")


def main():
    parser = argparse.ArgumentParser(
        description='统计指定目录下所有文件的字数（中文按字，英文按单词）'
    )
    parser.add_argument(
        '--dir',
        required=True,
        help='要统计的目标目录路径'
    )
    args = parser.parse_args()

    statistic_dir(args.dir)


if __name__ == '__main__':
    main()
