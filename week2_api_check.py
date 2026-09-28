#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 2 周 · 实现 Web 远程采集指令与执行结果反馈 —— 端到端验证脚本

用法:
    python week2_api_check.py http://<设备IP>
    例如: python week2_api_check.py http://10.132.124.63

逐项对应第 2 周"当堂验证"样例:
  1) 页面可打开，含"刷新已存数据"按钮与"请求—回执—观测记录"区块
  2) 刷新已存数据: 只读板上已保存观测，不触发采集 (triggered_read=false)
  3) 暂停周期上报: 已存观测序号冻结、数据年龄增长 (数据未更新 != 硬件故障)
  4) 命令通道保持可用: 下发采集 -> 已受理 -> 设备回执 -> 完成，新观测与请求号关联
  5) 重复点击: 沿用同一请求号 (Task in progress)
  6) 未知请求号: 返回 unknown，不把当前数值当作本次结果
  7) 设备端记录: 请求号 / 状态 / 来源 / 观测 seq / 受理->结果
  8) 恢复周期上报: 已存观测继续更新

只依赖 Python 标准库。
"""
import json
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://10.132.124.63'
PASS = []
FAIL = []


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=6) as r:
        return json.loads(r.read().decode('utf-8'))


def post_json(path):
    req = urllib.request.Request(BASE + path, method='POST', data=b'')
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode('utf-8'))


def show(tag, obj):
    print('  [' + tag + '] ' + json.dumps(obj, ensure_ascii=False))


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('%s %s %s' % ('[PASS]' if ok else '[FAIL]', name, detail))


def main():
    print('== Week 2 端到端验证 == ' + BASE)

    # 1) 页面
    print('\n[1] 页面自检')
    with urllib.request.urlopen(BASE + '/', timeout=6) as r:
        page = r.read().decode('utf-8', 'replace')
        code = r.status
    check('页面 200 且含两个对照按钮',
          code == 200 and ('刷新已存数据' in page) and ('采集一次最新数据' in page),
          'status=%d bytes=%d' % (code, len(page)))
    check('含请求—回执—观测记录区块与证据说明',
          ('evidence-log' in page) and ('证明方式' in page))

    # 2) 刷新已存数据（只读）
    print('\n[2] 刷新已存数据（不触发采集）')
    l1 = get_json('/api/observation/last')
    show('last', {k: l1.get(k) for k in ('valid', 'record_id', 'seq', 'source',
                                         'triggered_read', 'data_age_ms', 'stale')})
    check('刷新已存数据不触发采集', l1.get('triggered_read') is False)

    # 3) 暂停周期上报（注意：暂停前"在途"的一次采样可能仍会落地，最多 1 次）
    print('\n[3] 暂停周期上报（命令通道保持可用）')
    show('pause', post_json('/api/auto_refresh?on=0'))
    time.sleep(1.5)                       # 等暂停前在途的一次采样落地
    l2 = get_json('/api/observation/last')
    show('last-after-pause', {k: l2.get(k) for k in ('seq', 'data_age_ms', 'stale')})
    time.sleep(3)
    l2b = get_json('/api/observation/last')
    show('last-3s-later', {k: l2b.get(k) for k in ('seq', 'data_age_ms', 'stale')})
    check('暂停后观测序号冻结（3 s 内不再产生新观测）',
          l2b.get('seq') == l2.get('seq'), 'seq=%s' % l2b.get('seq'))
    check('暂停后数据年龄增长（数据未更新）',
          (l2b.get('data_age_ms') or 0) > (l2.get('data_age_ms') or 0),
          'age %s -> %s' % (l2.get('data_age_ms'), l2b.get('data_age_ms')))

    # 4) 命令通道下发采集
    print('\n[4] 采集一次最新数据（暂停周期上报状态下）')
    c = post_json('/api/collect')
    show('collect', c)
    rid = c.get('request_id')
    obs = None
    seq = []
    for _ in range(20):
        time.sleep(0.25)
        s = get_json('/api/collect/status?request_id=' + rid)
        if s.get('status') not in seq:
            seq.append(s.get('status'))
            show('status', {k: s.get(k) for k in
                            ('status', 'received_ms', 'completed_ms', 'linked')})
        if s.get('status') in ('completed', 'failed', 'timeout'):
            obs = s.get('observation') or {}
            check('状态链含设备回执阶段', 'received' in seq or s.get('received_ms', -1) >= 0,
                  ' -> '.join(seq))
            check('完成且新观测关联本次请求号',
                  s.get('status') == 'completed' and s.get('linked') is True and
                  obs.get('request_id') == rid)
            check('新观测为真实读取 source=live 且序号递增',
                  obs.get('source') == 'live' and (obs.get('seq') or 0) > (l2.get('seq') or 0),
                  'seq %s -> %s' % (l2.get('seq'), obs.get('seq')))
            break
    else:
        check('采集在等待窗口内完成', False, ' -> '.join(seq))

    # 5) 重复点击
    print('\n[5] 重复点击')
    d1 = post_json('/api/collect')
    d2 = post_json('/api/collect')
    show('dup', d2)
    check('任务进行中沿用同一请求号',
          d2.get('request_id') == d1.get('request_id') and
          d2.get('message') == 'Task in progress')

    # 6) 未知请求号
    print('\n[6] 未知请求号')
    u = get_json('/api/collect/status?request_id=req-does-not-exist')
    show('unknown', u)
    check('未知请求号不返回成功', u.get('status') == 'unknown' and u.get('linked') is False)

    # 7) 设备端记录
    print('\n[7] 请求—回执—观测记录')
    h = get_json('/api/collect/history')
    recs = h.get('records') or []
    if recs:
        show('record', recs[0])
    check('设备端有记录且含受理/回执/结果时间', len(recs) > 0 and
          all(k in recs[0] for k in ('submitted_ms', 'received_ms', 'completed_ms')))

    # 8) 恢复周期上报
    print('\n[8] 恢复周期上报')
    show('resume', post_json('/api/auto_refresh?on=1'))
    time.sleep(2.5)
    l3 = get_json('/api/observation/last')
    show('last-after-resume', {k: l3.get(k) for k in ('record_id', 'seq', 'source')})
    check('恢复后已存观测继续更新', (l3.get('seq') or 0) > (l2.get('seq') or 0))

    sv = get_json('/api/sensor')
    show('sensor', {k: sv.get(k) for k in
                    ('device_id', 'sensor_source', 'task_seq', 'obs_seq',
                     'direct_read_count', 'poll_count', 'timeout_ms',
                     'auto_refresh', 'time_quality')})

    print('\n== 汇总: %d PASS / %d FAIL ==' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAILED: ' + f)
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # noqa
        print('[ERROR] ' + str(exc))
        sys.exit(2)