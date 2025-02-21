#!/usr/bin/env python
import sys
import xml.etree.ElementTree as ET
import re
import os.path
import subprocess
from tabulate import tabulate
from pycobertura import Cobertura
import tempfile
import argparse


def parse_arguments():
    """コマンドライン引数の解析"""
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


def find_lines(j_package, filename):
    """Return all <line> elements for a given source file in a package."""
    lines = list()
    sourcefiles = j_package.findall("sourcefile")
    for sourcefile in sourcefiles:
        if sourcefile.attrib.get("name") == os.path.basename(filename):
            lines = lines + sourcefile.findall("line")
    return lines


def line_is_after(jm, start_line):
    return int(jm.attrib.get('line', 0)) > start_line


def method_lines(jmethod, jmethods, jlines):
    """Filter the lines from the given set of jlines that apply to the given jmethod."""
    start_line = int(jmethod.attrib.get('line', 0))
    larger = list(int(jm.attrib.get('line', 0))
                  for jm in jmethods if line_is_after(jm, start_line))
    end_line = min(larger) if len(larger) else 99999999

    for jline in jlines:
        if start_line <= int(jline.attrib['nr']) < end_line:
            yield jline


def convert_lines(j_lines, into):
    """Convert the JaCoCo <line> elements into Cobertura <line> elements, add them under the given element."""
    c_lines = ET.SubElement(into, 'lines')
    for jline in j_lines:
        mb = int(jline.attrib['mb'])
        cb = int(jline.attrib['cb'])
        ci = int(jline.attrib['ci'])

        cline = ET.SubElement(c_lines, 'line')
        cline.set('number', jline.attrib['nr'])
        # Probably not true but no way to know from JaCoCo XML file
        cline.set('hits', '1' if ci > 0 else '0')

        if mb + cb > 0:
            percentage = str(
                int(100 * (float(cb) / (float(cb) + float(mb))))) + '%'
            cline.set('branch',             'true')
            cline.set('condition-coverage', percentage +
                      ' (' + str(cb) + '/' + str(cb + mb) + ')')

            cond = ET.SubElement(ET.SubElement(
                cline, 'conditions'), 'condition')
            cond.set('number',   '0')
            cond.set('type',     'jump')
            cond.set('coverage', percentage)
        else:
            cline.set('branch', 'false')


def guess_filename(path_to_class):
    m = re.match('([^$]*)', path_to_class)
    return (m.group(1) if m else path_to_class) + '.java'


def add_counters(source, target):
    target.set('line-rate',   counter(source, 'LINE'))
    target.set('branch-rate', counter(source, 'BRANCH'))
    target.set('complexity', counter(source, 'COMPLEXITY', sum))


def fraction(covered, missed):
    return covered / (covered + missed)


def sum(covered, missed):
    return covered + missed


def counter(source, type, operation=fraction):
    cs = source.findall('counter')
    c = next((ct for ct in cs if ct.attrib.get('type') == type), None)

    if c is not None:
        covered = float(c.attrib['covered'])
        missed = float(c.attrib['missed'])

        return str(operation(covered, missed))
    else:
        return '0.0'


def convert_method(j_method, j_lines):
    c_method = ET.Element('method')
    c_method.set('name',      j_method.attrib['name'])
    c_method.set('signature', j_method.attrib['desc'])

    add_counters(j_method, c_method)
    convert_lines(j_lines, c_method)

    return c_method


def convert_class(j_class, j_package):
    c_class = ET.Element('class')
    c_class.set('name',     j_class.attrib['name'].replace('/', '.'))
    c_class.set('filename', guess_filename(j_class.attrib['name']))

    all_j_lines = list(find_lines(j_package, c_class.attrib['filename']))

    c_methods = ET.SubElement(c_class, 'methods')
    all_j_methods = list(j_class.findall('method'))
    for j_method in all_j_methods:
        j_method_lines = method_lines(j_method, all_j_methods, all_j_lines)
        c_methods.append(convert_method(j_method, j_method_lines))

    add_counters(j_class, c_class)
    convert_lines(all_j_lines, c_class)

    return c_class


def convert_package(j_package):
    c_package = ET.Element('package')
    c_package.attrib['name'] = j_package.attrib['name'].replace('/', '.')

    c_classes = ET.SubElement(c_package, 'classes')
    for j_class in j_package.findall('class'):
        c_classes.append(convert_class(j_class, j_package))

    add_counters(j_package, c_package)

    return c_package


def convert_root(source, target, source_roots):
    target.set('timestamp', str(
        int(source.find('sessioninfo').attrib['start']) / 1000))

    sources = ET.SubElement(target, 'sources')
    for s in source_roots:
        ET.SubElement(sources, 'source').text = s

    packages = ET.SubElement(target, 'packages')
    for package in source.findall('package'):
        packages.append(convert_package(package))

    add_counters(source, target)


def jacoco2cobertura(filename, source_roots):
    if filename == '-':
        root = ET.fromstring(sys.stdin.read())
    else:
        tree = ET.parse(filename)
        root = tree.getroot()

    into = ET.Element('coverage')
    convert_root(root, into, source_roots)
    print('<?xml version="1.0" ?>')
    print(ET.tostring(into))


def convert_and_analyze_coverage(args):
    """JaCoCoのXMLを変換してカバレッジ分析を行う"""
    # JaCoCo XMLをCobertura形式に変換
    try:
        tree = ET.parse(args.jacoco_xml)
    except Exception as e:
        print(f"Error: Failed to parse JaCoCo XML file: {e}")
        sys.exit(1)

    root = tree.getroot()
    cobertura_root = ET.Element('coverage')
    convert_root(root, cobertura_root, args.source_roots)

    # 一時的にCobertura XMLを保存して分析
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as temp_file:
        temp_file.write('<?xml version="1.0" ?>\n')
        temp_file.write(ET.tostring(cobertura_root, encoding='unicode'))
        temp_file.flush()

        # 変更されたファイルのリストを取得
        changed_files = get_changed_files(args.base_branch)
        if not changed_files:
            print(f"No Java files changed compared to {
                  args.base_branch} branch")
            return

        # Coberturaオブジェクトを作成して分析
        try:
            cobertura = Cobertura(temp_file.name)
        except Exception as e:
            print(f"Error: Failed to analyze coverage data: {e}")
            sys.exit(1)

        coverage_data = []

        for file_path in changed_files:
            try:
                # ソースルートに基づいてファイルパスを調整
                relative_path = None
                for root in args.source_roots:
                    if file_path.startswith(root):
                        relative_path = file_path[len(root):].lstrip('/')
                        break
                if not relative_path:
                    continue

                file_coverage = {
                    'file': file_path,
                    'line_rate': cobertura.line_rate(relative_path),
                    'branch_rate': cobertura.branch_rate(relative_path),
                    'coverage_link': os.path.join(args.jacoco_html_dir,
                                                  relative_path.replace('.java', '.html'))
                }
                coverage_data.append(file_coverage)
            except Exception as e:
                print(f"Warning: Could not get coverage for {file_path}: {str(e)}",
                      file=sys.stderr)

        # 結果の表示
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
    convert_and_analyze_coverage(args)


if __name__ == '__main__':
    main()
