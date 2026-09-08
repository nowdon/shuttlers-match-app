"""Build isolated, allowlisted stage roots; never copy runtime/secret files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
STAGES = ['A', 'B', 'C', 'D-off', 'D-on', 'E-off', 'E-on', 'F', 'G', 'H', 'I', 'J', 'H-off', 'H-cached']


def build(stage):
    assert stage in STAGES
    target = ROOT / '.build' / stage
    assert not target.exists(), 'Use a fresh stage directory; never overwrite prior evidence'
    src = target / 'src'
    src.mkdir(parents=True)
    level = stage[0]
    guard = not stage.endswith('-off') and (stage.endswith('-on') or level in 'FGHIJ')
    deps = [] if level == 'A' else ['flask==3.0.3']
    if level >= 'C':
        deps += ['flask-sqlalchemy==3.1.1', 'tzdata==2025.3']
    project = (ROOT / 'pyproject.toml').read_text().replace('dependencies = []', 'dependencies = ' + json.dumps(deps))
    (target / 'pyproject.toml').write_text(project)
    # Locked profiles make every stage reproducible without another PoC.
    profile = 'minimal' if level == 'A' else 'flask' if level == 'B' else 'app'
    for name in ['pylock.toml', 'uv.lock']:
        shutil.copyfile(ROOT / 'profiles' / profile / name, target / name)
    def link(source, name):
        dest = src / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(os.path.relpath(source, dest.parent))
    if level >= 'C':
        link(REPO / 'models.py', 'models.py')
    if level >= 'D':
        for name in ['app.py','logic.py']:
            link(REPO / name, name)
        for directory in ['routes','data','utils']:
            for source in sorted((REPO / directory).glob('*.py')):
                link(source, str(source.relative_to(REPO)))
    if guard:
        link(ROOT / 'src/side_effect_guard.py', 'side_effect_guard.py')
    if level >= 'G':
        link(ROOT / 'src/probe.html', 'templates/probe.html')
        for source in sorted((REPO / 'templates').glob('*.html')):
            link(source, 'templates/' + source.name)
    if level == 'J':
        link(REPO / 'static/participants_template.csv', 'static/participants_template.csv')
        for source in sorted((REPO / 'static/cards').glob('*.png')):
            link(source, 'static/cards/' + source.name)
    if level == 'A':
        code = (ROOT / 'src/minimal.py').read_text()
    elif level == 'B':
        code = 'import flask\n' + (ROOT / 'src/minimal.py').read_text().replace('stage A ok','stage B ok').replace("'A'","'B'")
    elif level == 'C':
        code = 'import flask, flask_sqlalchemy, sqlalchemy, tzdata, models\n' + (ROOT / 'src/minimal.py').read_text().replace('stage A ok','stage C ok').replace("'A'","'C'")
    else:
        code = (ROOT / 'src/staged_worker.py').read_text().replace('STAGE_VALUE', repr(stage)).replace('GUARD_VALUE', repr(guard))
    (src / 'worker.py').write_text(code)
    config = {'name':'shuttlers-match-stability-poc','main':'src/worker.py','compatibility_date':'2026-09-08','compatibility_flags':['python_workers'],'workers_dev':True,'preview_urls':False}
    if level in 'IJ':
        # Only the explicit, approved public CSV and card PNG files.
        assets = target / 'assets'
        for source in [REPO/'static/participants_template.csv', *sorted((REPO/'static/cards').glob('*.png'))]:
            dest = assets / source.relative_to(REPO/'static')
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.symlink_to(os.path.relpath(source,dest.parent))
        config['assets']={'directory':'assets','binding':'ASSETS','run_worker_first':True}
    if level == 'J':
        config['rules']=[{'type':'Data','globs':['**/*.csv','**/*.png'],'fallthrough':True}]
    (target/'wrangler.jsonc').write_text(json.dumps(config,indent=2)+'\n')
    manifest = {'stage':stage,'guard':guard,'dependencies':deps,'wrangler':config,'source_files':{str(f.relative_to(src)):hashlib.sha256(f.read_bytes()).hexdigest() for f in src.rglob('*') if f.is_file()},'asset_count':55 if level in 'IJ' else 0}
    (ROOT/'results'/f'{stage}-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return target


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('stage', choices=STAGES)
    print(build(args.parse_args().stage))
