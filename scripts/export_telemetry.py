"""Publish a finished telemetry experiment; never export credentials or hidden run caches."""
import argparse
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def export(source,dest):
    dest.mkdir(parents=True,exist_ok=True)
    def copy(p,target):
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    for name in ('preregistered_plan.md','frozen_methods.py','manifest.json','frozen.json','request_plan.json','api_results.jsonl','labels.json','summary.json'):
        copy(source/name,dest/name)
    copy(source/'setup_private.json',dest/'setup_revealed_after_decisions.json')
    for folder in ('annotations','cases'):
        for p in (source/folder).rglob('*'):
            if p.is_file():copy(p,dest/p.relative_to(source))
    for p in (source/'scoring').rglob('*.png'):
        copy(p,dest/p.relative_to(source))
    summary=json.loads((source/'summary.json').read_text())
    with (dest/'per_state.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['case','episode','label','A','B','A_choices','B_choices'],lineterminator='\n')
        w.writeheader();w.writerows(summary['table'])
    versions={p:importlib.metadata.version(p) for p in ('mujoco','numpy','scipy','pydantic','httpx','matplotlib')}
    (dest/'environment.json').write_text(json.dumps(versions,indent=2))
    fig,axes=plt.subplots(1,3,figsize=(11,4.2))
    groups=summary['groups'];colors=['#55758c','#37837a'];names=['A: existing feedback','B: + mechanical history']
    for ax,field,title in zip(axes,('accuracy','false_retained_rate','coverage'),('Correct retention assessment','False retained / failed cases','Answered rather than abstained')):
        values=[100*groups[g][field] if groups[g][field] is not None else 0 for g in ('A','B')]
        bars=ax.bar(['A','B'],values,color=colors,width=.6)
        ax.bar_label(bars,labels=[f'{v:.1f}%' for v in values],padding=3)
        ax.set_ylim(0,110);ax.set_yticks([0,25,50,75,100]);ax.set_title(title,fontsize=10)
        ax.spines[['top','right']].set_visible(False);ax.yaxis.grid(True,alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('Robot telemetry pilot: paired offline grasp diagnosis',fontsize=13)
    fig.text(.5,.04,'A: shared visual description + TCP + binary finger contacts. B: A + sampled robot feedback.\n24 frozen states, 6 related fixture trajectories, 3 choice permutations. Not end-to-end grasp success.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.12,1,.94))
    fig.savefig(dest/'comparison.png',dpi=180);fig.savefig(dest/'comparison.pdf');plt.close(fig)
    # Post-hoc illustrations, explicitly excluded from the primary comparison.
    # Fixed cases show empty closure, retained grip, and contact without retention.
    fig,axes=plt.subplots(3,3,figsize=(12,7.5))
    for row,cid in enumerate(('C01','C02','C12')):
        a=np.load(source/'cases'/cid/'robot_telemetry.npz')['samples']
        receipt=json.loads((source/'cases'/cid/'receipt.json').read_text())
        t=a[:,0]-a[-1,0];action_start=receipt['before_simulation_s']-a[-1,0]
        axes[row,0].plot(t,a[:,73]*1000,color='#55758c');axes[row,0].set_ylabel(cid)
        for col,index,scale in ((1,44,1),(2,26,1000)):
            axes[row,col].plot(t,a[:,index]*scale,label='finger 1',color='#55758c')
            axes[row,col].plot(t,a[:,index+1]*scale,label='finger 2',color='#37837a')
        for ax in axes[row]:
            ax.axvline(action_start,color='#999',ls='--',lw=.8)
            ax.grid(True,alpha=.2);ax.set_xlabel('seconds before frozen observation')
    for ax,title in zip(axes[0],('Pad gap (mm)','Finger actuator output (N)','Finger joint velocity (mm/s)')):ax.set_title(title,fontsize=10)
    axes[0,1].legend(fontsize=8)
    fig.suptitle('Post-hoc signal illustrations: C01 failed, C02 retained, C12 failed despite contact',fontsize=12)
    fig.tight_layout(rect=(0,0,1,.95));fig.savefig(dest/'signal_examples.png',dpi=150);plt.close(fig)
    hashes={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob('*') if p.is_file() and p.name!='artifact_integrity.json'}
    (dest/'artifact_integrity.json').write_text(json.dumps({'exported_wall_time':time.time(),'files':hashes},indent=2))
    print(dest)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=ROOT/'runs/telemetry_pilot_01');p.add_argument('--dest',type=Path,default=ROOT/'experiments/results/telemetry_pilot_01')
    args=p.parse_args();export(args.source,args.dest)
