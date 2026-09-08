"""Before every deploy, verify inputs and dry-run outputs against allowlists."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent


def audit(stage,bundle):
    build=ROOT/'.build'/stage
    config=json.loads((build/'wrangler.jsonc').read_text())
    assert config['name']=='shuttlers-match-stability-poc'
    assert config['workers_dev'] and not config.get('preview_urls')
    assert set(config)<={'name','main','compatibility_date','compatibility_flags','workers_dev','preview_urls','assets','rules'}
    assert not list(build.glob('.env*')) and not list(build.glob('.dev.vars*'))
    approved={str(p.relative_to(build/'src')) for p in (build/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    bundle=Path(bundle)
    files=[p for p in bundle.rglob('*') if p.is_file()]
    assert files
    for p in files:
        relative=p.relative_to(bundle)
        assert p.name not in ['config.json','match_state.json','draft_state.json']
        assert p.suffix!='.db' and 'instance' not in relative.parts and 'history_dumps' not in relative.parts
        assert not p.name.startswith(('.env','.dev.vars'))
        if relative.parts[0]!='python_modules' and str(relative)!='README.md':
            assert str(relative) in approved,str(relative)
            assert p.read_bytes()==(build/'src'/relative).read_bytes()
        for key,value in os.environ.items():
            if any(word in key for word in ['SECRET','TOKEN','PASSWORD','CREDENTIAL']) and len(value)>=8:
                assert value.encode() not in p.read_bytes(),'Credential match suppressed'
    static=[p for p in files if p.relative_to(bundle).parts[0]=='static']
    if stage[0]!='J':assert not static
    if stage=='A':
        assert not any('flask' in str(p.relative_to(bundle)).lower() for p in files)
    r={'stage':stage,'bundle_files':len(files),'python_filesystem_static_files':len(static),'forbidden_files':0,'credential_matches':0,'guard':json.loads((ROOT/'results'/f'{stage}-manifest.json').read_text())['guard']}
    (ROOT/'results'/f'{stage}-audit.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r),flush=True)


if __name__=='__main__':audit(sys.argv[1],sys.argv[2])
