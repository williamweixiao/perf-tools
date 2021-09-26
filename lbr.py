#!/usr/bin/env python
# A module for processing LBR streams
# Author: Ahmad Yasin
# edited: Sep. 2021
#
from __future__ import print_function
__author__ = 'ayasin'
debug = 0

import common as C
import re, sys

def read_line():
  line = sys.stdin.readline()
  return line

def skip_sample(s):
  line = read_line()
  while not re.match(r"^$", line):
    line = read_line()
    assert line, 'was input truncated? sample:\n%s'%s
  return 0

def print_sample(sample, n=10):
  print(sample[0])
  print('\n'.join(sample[-n:]))

def line_ip(line):
  x = re.match(r"\s+(\S+)\s+(\S+)", line)
  assert x, 'expect <address> at left of %s'%line
  ip = x.group(1).lstrip("0")
  return int(ip, 16)

def hex(ip): return '0x%x'%ip

loops = {}
stat = {x: 0 for x in ('bad', 'bogus', 'total')}
stat['IPs'] = {}
stat['size'] = {'min': 0, 'max': 0, 'avg': 0}
size_sum=0

bwd_br_tgts = [] # better make it local to read_sample..
def read_sample(ip_filter=None, skip_bad=True, min_lines=0, labels=False):
  global size_sum, bwd_br_tgts
  valid, lines, bwd_br_tgts = 0, [], []
  size_stats_en = skip_bad and not labels
  
  def detect_loop(line,
    MOLD=4e4): #Max Outer Loop Distance
    global bwd_br_tgts #unlike nonlocal, global works in python2 too!
    def find_block_ip():
      x = len(lines)-2
      while x>=0:
        if is_taken(lines[x]):
          return line_ip(lines[x+1])
        x -= 1
      return 0
    ip = line_ip(line)
    if ip in loops:
      loops[ip]['hotness'] += 1
      if not loops[ip]['entry-block'] and not is_taken(lines[-1]):
        loops[ip]['entry-block'] = find_block_ip()
      return
    xip = line_ip(lines[-1])
    # only simple loops that are entirely observed in a single sample are supported
    if ip in bwd_br_tgts:
      inner, outer = 0, 0
      ins, outs = set(), set()
      for l in loops:
        if ip > l and xip < loops[l]['back']:
          inner += 1
          outs.add(hex(l))
          loops[l]['outer'] = 1
          loops[l]['inner-loops'].add(hex(ip))
        if ip < l and xip > loops[l]['back']:
          outer = 1
          ins.add(hex(l))
          loops[l]['inner'] += 1
          loops[l]['outer-loops'].add(hex(ip))
      loops[ip] = {'back': xip, 'hotness': 1,
        'entry-block': 0 if xip > ip else find_block_ip(),
        'inner': inner, 'outer': outer, 'inner-loops': ins, 'outer-loops': outs
      }
      #todo: +tripcount, +loop length
      bwd_br_tgts.remove(ip)
      return
    if is_taken(lines[-1]) and ip < xip and\
      ((xip - ip) < MOLD) and\
      not ('call' in lines[-1] or 'ret' in lines[-1]): #these require --xed with perf script
      bwd_br_tgts += [ip]
  
  while not valid:
    valid, lines, bwd_br_tgts = 1, [], []
    stat['total'] += 1
    while True:
      line = read_line()
      # input ended
      if not line:
        if size_stats_en:
          total = stat['IPs'][ip_filter] if ip_filter else stat['total']
          stat['size']['avg'] = round(size_sum / (total - stat['bad'] - stat['bogus']), 1)
        if len(lines):
          stat['bogus'] += 1
          if not skip_bad: return lines
        return None
      # a new sample started
      #             perf  3433 1515065.348598:    1000003 EVENT.NAME:      7fd272e3b217 __regcomp+0x57 (/lib/x86_64-linux-gnu/libc-2.23.so)
      if ip_filter and len(lines) == 0:
        if not ip_filter in line:
          valid = skip_sample(lines[0])
          break
        if not ip_filter in stat['IPs']: stat['IPs'][ip_filter] = 0
        stat['IPs'][ip_filter] += 1
      # a sample ended
      if re.match(r"^$", line):
        len_m1 = len(lines)-1
        ip = int(C.str2list(lines[0])[5], 16)
        if len_m1 == 0 or\
           min_lines and (len_m1 < min_lines) or\
           ip != line_ip(lines[len_m1]):
          valid = 0
          stat['bogus'] += 1
        break
      # invalid sample is about to end
      if skip_bad and 'not reaching sample' in line:
        valid = 0
        stat['bad'] += 1
        assert re.match(r"^$", read_line())
        break
      # a line with a label
      if not labels and is_label(line):
        continue
      # e.g. "        prev_nonnote_           addb  %al, (%rax)"
      if skip_bad and len(lines) and not line.strip().startswith('0'):
        valid = skip_sample(lines[0])
        stat['bogus'] += 1
        break
      # an instruction following a taken
      if len(lines) > 1 and not is_label(line):
        detect_loop(line)
      lines += [ line.rstrip('\r\n') ]
  if size_stats_en:
    size = len(lines) - 1
    if size_sum == 0: stat['size']['min'] = stat['size']['max'] = size
    else:
      if debug and size < 64:
        print(stat['total'])
        print_sample(lines)
      if stat['size']['min'] > size: stat['size']['min'] = size
      if stat['size']['max'] < size: stat['size']['max'] = size
    size_sum += size
  return lines

def is_jmp_next(br, # a hacky implementation for now
  JS=2,             # short direct Jump Size
  CDLA=16):         # compiler default loops alignment
  mask = ~(CDLA - 1)
  return (br['to'] == (br['from'] + JS)) or (
         (br['to'] & mask) ==  ((br['from'] & mask) + CDLA))

def is_label(line):   return line.strip().endswith(':')

def is_loop(line):    return line_ip(line) in loops

def is_taken(line):   return '#' in line

def get_loop(ip):     return loops[ip] if ip in loops else None

def get_taken(sample, n):
  assert n in range(-32, 0), 'invalid n='+str(n)
  i = len(sample)-1
  frm, to = -1, -1
  while i >= 0:
    if is_taken(sample[i]):
      n += 1
      if n==0:
        frm = line_ip(sample[i])
        if i < (len(sample)-1): to = line_ip(sample[i+1])
        break
    i -= 1
  return {'from': frm, 'to': to, 'taken': 1}

def print_all(nloops=5):
  stat['detected-loops'] = len(loops)
  print(stat)
  print('top %d loops:'%nloops)
  cnt=0
  sloops = sorted(loops.items(), key=lambda x: loops[x[0]]['hotness'], reverse=True)
  for l in sloops:
    print_loop(l[0])
    cnt += 1
    if cnt >= nloops: break

def print_br(br):
  print('[from: 0x%x, to: 0x%x, taken: %d]'%(br['from'], br['to'], br['taken']))

def print_loop(ip):
  loop = loops[ip]
  print('[ip: %s, hotness: %6d, '%(hex(ip), loop['hotness']), end='')
  del loop['hotness']
  for x in ('back', 'entry-block'):
    print('%s: %s, '%(x, hex(loop[x])), end='')
    del loop[x]
  details = C.chop(str(loop), (")'", 'set('))
  print('%s]'%details)

