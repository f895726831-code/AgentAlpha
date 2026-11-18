#!/usr/bin/env python3
"""Apply T2 plans to portfolio.json"""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('portfolio.json', 'r', encoding='utf-8') as f:
    p = json.load(f)

plans = {
    '688008.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27重评：原计划195-200已失效，价格171已跌破185止损线。52w高332→现价171，-48%，RSI=42。DDR5全球龙头基本面不变。新入场150-170(恐慌区+前低111.66上方)，止损143(-5%)，止盈185/200。等缩量企稳信号。】',
        'entry_low': 150, 'entry_high': 170,
        'stop': 143, 'target': 185,
        'status': '🔴 risk_off禁建仓。原计划已价格穿透，新计划等恐慌企稳。'
    },
    '300666.SZ': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27重评：原计划232-238完全失效，价格已跌至158.91(-31.5%低于入场区，跌破215止损)。52w高419→现价159，-62%，RSI=9.9极度超卖。靶材+零部件双轮驱动不变。新入场140-160(52w低81上方+恐慌区)，止损133(-5%)，止盈175/190。需等RSI回升+缩量。】',
        'entry_low': 140, 'entry_high': 160,
        'stop': 133, 'target': 175,
        'status': '🔴 risk_off禁建仓。极度超卖(RSI=9.9)，等反弹确认。'
    },
    '688347.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27重评：原计划240-248完全失效，现价156.18(-35%低于入场区，跌破218止损)。52w高413→现价156，-62%，RSI=14.1深度超卖。新入场135-155(前低97上方)，止损128(-5.2%)，止盈170/185。等缩量+RSI回升。】',
        'entry_low': 135, 'entry_high': 155,
        'stop': 128, 'target': 170,
        'status': '🔴 risk_off禁建仓。深度超卖(RSI=14)，等底部确认。'
    },
    '688690.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27更新：价格37.18略低于原入场区38-39.50(-2.2%)，仍在合理范围。色谱填料国产龙头，H1营收+37%/利润+116%。入场维持38-39.50(回踩均价)，止损34.50，目标45/50。若继续下跌可在35-37加第二批。】',
        'entry_low': 38.00, 'entry_high': 39.50,
        'stop': 34.50, 'target': 50,
        'status': '⏳ 价格略低于入场区间，等待反弹至38+或第二批35-37。risk_off禁建仓。'
    },
    '688012.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高500→245，-51%，RSI=8极度超卖。半导体刻蚀设备龙头。入场210-240(52w低172上方)，止损200，止盈260/280。等RSI回升至30+且缩量。】',
        'entry_low': 210, 'entry_high': 240,
        'stop': 200, 'target': 260,
        'status': '🔴 极度超卖(RSI=8)，等反弹信号。risk_off禁建仓。科创板200股。'
    },
    '002371.SZ': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高967→502，-48%，RSI=9极度超卖。综合半导体设备龙头。入场440-490(52w低386上方)，止损418，止盈540/580。等RSI回升。】',
        'entry_low': 440, 'entry_high': 490,
        'stop': 418, 'target': 540,
        'status': '🔴 极度超卖(RSI=9)，等反弹信号。risk_off禁建仓。主板100股。'
    },
    '688041.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高395→192，-51%，RSI=33(已脱离超卖区)。国产CPU/GPU龙头，深算DCU对标A100。入场186-192(52w低186附近强支撑)，止损177(跌破前低止损)，止盈210/225。等站稳190不破186。】',
        'entry_low': 186, 'entry_high': 192,
        'stop': 177, 'target': 210,
        'status': '🟡 RSI=33已脱离超卖，186(52w低)是关键支撑。risk_off禁建仓。科创板200股。'
    },
    '300750.SZ': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高467→234，-50%，RSI=28接近超卖。全球动力电池龙头市占率37%。入场215-230(52w低216.5上方)，止损205，止盈250/270。等RSI回升+缩量企稳。】',
        'entry_low': 215, 'entry_high': 230,
        'stop': 205, 'target': 250,
        'status': '🔴 RSI=28接近超卖，等216支撑确认。risk_off禁建仓。创业板100股。'
    },
    '688072.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高944→476，-50%，RSI=16深度超卖。半导体薄膜设备龙头，Alpha#1。入场420-470(52w低280上方有距离)，止损400，止盈510/550。等缩量+RSI回升至25+。】',
        'entry_low': 420, 'entry_high': 470,
        'stop': 400, 'target': 510,
        'status': '🔴 深度超卖(RSI=16)，等企稳信号。risk_off禁建仓。科创板200股。'
    },
    '300502.SZ': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高619→251，-59%，RSI=8极度超卖。光模块龙头，800G/1.6T放量。入场220-245(52w低205上方)，止损209，止盈265/290。极度超卖等RSI回升+缩量确认。】',
        'entry_low': 220, 'entry_high': 245,
        'stop': 209, 'target': 265,
        'status': '🔴 极度超卖(RSI=8)，切忌接飞刀。等RSI回升至25+。risk_off禁建仓。创业板100股。'
    },
    '603986.SH': {
        'strategy': 'T2_pullback_deep',
        'note_suffix': '【10/27 T2计划：52w高847→278，-67%(自选池最深跌幅)，RSI=34。NOR Flash+MCU+DRAM龙头。入场250-275(52w低176上方较远，需等进一步缩量筑底)，止损238，止盈300/320。存储涨价周期不变。】',
        'entry_low': 250, 'entry_high': 275,
        'stop': 238, 'target': 300,
        'status': '🔴 -67%最深跌幅，RSI=34脱离超卖但仍在下降。等底部确认。risk_off禁建仓。主板100股。'
    },
    '002050.SZ': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价34.53，MA20=33.80(+2.2%站上MA20)，RSI=63。热管理龙头+机器人关节散热。入场32.5-34.5(回踩MA20)，止损30.9(-5%)，止盈37.5/40。已站稳MA20是积极信号。】',
        'entry_low': 32.5, 'entry_high': 34.5,
        'stop': 30.9, 'target': 37.5,
        'status': '🟢 站上MA20(34.53>33.80)，RSI=63。等回调至33附近。risk_off禁建仓。主板100股。'
    },
    '002594.SZ': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价82.70≈MA20(82.93)，RSI=55。新能源全球龙头。入场78-83(MA20±2%)，止损74(-5%)，止盈90/96。方向不明需等趋势选择。】',
        'entry_low': 78, 'entry_high': 83,
        'stop': 74, 'target': 90,
        'status': '🟡 MA20走平(82.70≈82.93)，等方向确认。risk_off禁建仓。主板100股。'
    },
    '603893.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价199.99，MA20=201.82(-0.9%)，RSI=51。AIoT芯片龙头。入场190-200(MA20附近+支撑)，止损180(-5%)，止盈216/230。5日+5.79%短期偏强。】',
        'entry_low': 190, 'entry_high': 200,
        'stop': 180, 'target': 216,
        'status': '🟡 接近MA20，RSI=51中性。等回调至190-195。risk_off禁建仓。主板100股。'
    },
    '603019.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价76.30≈MA20(76.43)，RSI=53。国产算力龙头。入场72-77(MA20±3%)，止损68.4(-5%)，止盈83/89。5日+1.92%短期企稳。】',
        'entry_low': 72, 'entry_high': 77,
        'stop': 68.4, 'target': 83,
        'status': '🟡 MA20走平，RSI=53中性。等回调至72-74。risk_off禁建仓。主板100股。'
    },
    '688256.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价944，MA20=977(-3.4%)，RSI=50。国产AI芯片龙头，思元系列。入场880-950(MA20下方回调)，止损836(-5%)，止盈1026/1100。5日+2.51%企稳中。科创板200股。】',
        'entry_low': 880, 'entry_high': 950,
        'stop': 836, 'target': 1026,
        'status': '🟡 RSI=50中性，等回调至900附近。risk_off禁建仓。科创板200股。'
    },
    '688385.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价50.96，MA20=53.90(-5.5%)，RSI=42。国产FPGA龙头。入场47-52(低于MA20回调区)，止损44.7(-5%)，止盈56/60。等RSI企稳+缩量。科创板200股。】',
        'entry_low': 47, 'entry_high': 52,
        'stop': 44.7, 'target': 56,
        'status': '🟡 RSI=42偏弱，等47-50支撑确认。risk_off禁建仓。科创板200股。'
    },
    '688327.SH': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27 T2计划：现价10.35，MA20=9.72(+6.5%站上MA20)，RSI=65偏强。AI四小龙，人机协同。入场9.5-10.0(回踩MA20)，止损9.0(-5%)，止盈10.8/11.5。唯一站上MA20的AI标的，等待回踩。】',
        'entry_low': 9.5, 'entry_high': 10.0,
        'stop': 9.0, 'target': 10.8,
        'status': '🟢 站上MA20(+6.5%)，20日+3.71%趋势偏多。等回踩9.5-10。risk_off禁建仓。科创板200股。'
    },
    '002475.SZ': {
        'strategy': 'T2_pullback',
        'note_suffix': '【10/27新T2计划：7/30止损后现价49.85，MA20=48.55(+2.7%站上MA20)，RSI=64。消费电子龙头+汽车电子。入场46-50(回踩MA20±4%)，止损43.7(-5%)，止盈54/58。5日+4.97%短期偏强。】',
        'entry_low': 46, 'entry_high': 50,
        'stop': 43.7, 'target': 54,
        'status': '🟢 站上MA20(49.85>48.55)，RSI=64。等回调至47-48。risk_off禁建仓。主板100股。'
    },
}

updated_count = 0
for item in p['watchlist']:
    code = item['code']
    if code in plans:
        plan = plans[code]
        item['strategy'] = plan['strategy']
        old_note = item.get('note', '')
        if '【待补T2计划：' in old_note:
            old_note = old_note.split('【待补T2计划：')[0].strip()
        item['note'] = old_note + ' ' + plan['note_suffix']
        item['stop_loss'] = plan['stop']
        item['target_price'] = plan['target']
        item['target_range'] = [plan['entry_low'], plan['entry_high']]
        if 'plan' not in item or not isinstance(item.get('plan'), dict):
            item['plan'] = {}
        mid = round((plan['entry_low'] + plan['entry_high']) / 2, 2)
        item['plan']['entry'] = {'price': mid, 'note': f'区间{plan["entry_low"]}-{plan["entry_high"]}'}
        item['plan']['stop_loss'] = plan['stop']
        item['plan']['take_profit_t1'] = plan['target']
        item['plan']['status'] = plan['status']
        item['revised'] = '2026-10-27'
        updated_count += 1

p['updated'] = '2026-10-27T12:30:00'

with open('portfolio.json', 'w', encoding='utf-8') as f:
    json.dump(p, f, ensure_ascii=False, indent=2)

print(f'Updated {updated_count} A-share stocks with T2 plans')

# Print summary
for item in p['watchlist']:
    code = item['code']
    if code in plans:
        plan = plans[code]
        name = item['name']
        strategy = plan['strategy']
        el = plan['entry_low']
        eh = plan['entry_high']
        stop = plan['stop']
        target = plan['target']
        print(f'{code} {name}: {strategy} entry={el}-{eh} stop={stop} target={target}')