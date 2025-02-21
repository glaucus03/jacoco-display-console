#!/usr/bin/env python
import sys
import xml.etree.ElementTree as ET
import os.path
import subprocess
from tabulate import tabulate
import argparse


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Analyze code coverage for changed files based on JaCoCo report',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        '--jacoco-xml',
        required=True,
        help='Path to jacoco.xml file'
    )

    parser.add_argument(
        '--source-roots',
        required=True,
        nargs='+',
        help='Source root directories (e.g., src/main/java)'
    )

    parser.add_argument(
        '--base-branch',
        default='develop',
        help='Base branch to compare changes against'
    )

    parser.add_argument(
        '--coverage-threshold',
        type=float,
        default=80.0,
        help='Coverage threshold percentage for warnings'
    )

    parser.add_argument(
        '--jacoco-html-dir',
        default='target/site/jacoco',
        help='Directory containing JaCoCo HTML reports'
    )

    parser.add_argument(
        '--output-format',
        choices=['grid', 'simple', 'pipe', 'orgtbl'],
        default='grid',
        help='Output table format'
    )

    return parser.parse_args()


def get_changed_files(base_branch):
    """指定されたブランチとの差分ファイルを取得"""
    cmd = ['git', 'diff', '--name-only', base_branch]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: Failed to get diff against {base_branch}")
        print(f"Git error: {result.stderr}")
        sys.exit(1)
    return [f for f in result.stdout.splitlines() if f.endswith('.java')]


def get_coverage_from_jacoco(jacoco_xml, source_file):
    """JaCoCo XMLから直接カバレッジ情報を取得"""
    tree = ET.parse(jacoco_xml)
    root = tree.getroot()

    # パッケージ、クラス、ソースファイルを探索
    for package in root.findall('.//package'):
        for sourcefile in package.findall('sourcefile'):
            if os.path.basename(source_file) == sourcefile.get('name'):
                # カウンターからカバレッジを計算
                counters = sourcefile.findall('counter')
                line_rate = 0
                branch_rate = 0
                for counter in counters:
                    if counter.get('type') == 'BRANCH':
                        covered = int(counter.get('covered', 0))
                        missed = int(counter.get('missed', 0))
                        total = covered + missed
                        branch_rate = (covered / total) if total > 0 else 1.0
                    elif counter.get('type') == 'LINE':
                        covered = int(counter.get('covered', 0))
                        missed = int(counter.get('missed', 0))
                        total = covered + missed
                        line_rate = (covered / total) if total > 0 else 1.0

                return {
                    'line_rate': line_rate,
                    'branch_rate': branch_rate,
                    'package': package.get('name')
                }

    return None


def analyze_coverage(args):
    """カバレッジ分析を実行"""
    changed_files = get_changed_files(args.base_branch)
    if not changed_files:
        print(f"No Java files changed compared to {args.base_branch} branch")
        return

    coverage_data = []
    for file_path in changed_files:
        try:
            coverage = get_coverage_from_jacoco(
                args.jacoco_xml, os.path.basename(file_path))
            if coverage:
                coverage_data.append({
                    'file': file_path,
                    'line_rate': coverage['line_rate'],
                    'branch_rate': coverage['branch_rate'],
                    'coverage_link': os.path.join(
                        args.jacoco_html_dir,
                        coverage['package'].replace('.', '/'),
                        os.path.basename(file_path).replace('.java', '.html')
                    )
                })
        except Exception as e:
            print(f"Warning: Could not get coverage for {file_path}: {str(e)}",
                  file=sys.stderr)

    display_coverage_results(coverage_data, args)


def display_coverage_results(coverage_data, args):
    """カバレッジ結果をテーブル形式で表示"""
    headers = ["File", "Line Coverage", "Branch Coverage", "Coverage Report"]
    table_data = [
        [
            data['file'],
            f"{data['line_rate']*100:.1f}%",
            f"{data['branch_rate']*100:.1f}%",
            data['coverage_link']
        ] for data in coverage_data
    ]

    print(
        f"\nCoverage Report for Changed Files (comparing against {args.base_branch}):")
    print(tabulate(table_data, headers=headers, tablefmt=args.output_format))

    # 閾値未満のファイルの警告
    low_coverage = [d for d in coverage_data if
                    d['line_rate']*100 < args.coverage_threshold or
                    d['branch_rate']*100 < args.coverage_threshold]

    if low_coverage:
        print(f"\nWarning: Following files have coverage below {
              args.coverage_threshold}%:")
        for data in low_coverage:
            print(f"- {data['file']}")
            print(f"  Line Coverage: {data['line_rate']*100:.1f}%")
            print(f"  Branch Coverage: {data['branch_rate']*100:.1f}%")


def main():
    args = parse_arguments()
    analyze_coverage(args)


if __name__ == '__main__':
    main()
