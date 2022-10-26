#!/usr/bin/env python3

import sys
import re
import json

if not sys.stdout.isatty():
  sys.stdout.reconfigure(encoding='utf8')

SKIP_START = '</span></font>'
SKIP_END = '<font size="3" face="Times">'

T_QUESTION = 'Питання:'
T_EXPLANATION = 'Пояснення:'
T_ANSWERS = 'Відповіді:'

rx_question_start = re.compile(r'^\d+\. (.*)$')
rx_option_start = re.compile(r'^(\d+)\) (.*)$')
rx_offset_line = re.compile(r'left:(\d+)"><nobr>(.*?)</nobr>')


def read(name):
  with open(name, 'r', encoding='utf8') as fp:
    for line in fp:
      yield {
        'text': line.strip(),
      }


def skip_page_numbers(lines):
  keep = True

  for line in lines:
    text = line['text']
    if text.startswith(SKIP_START):
      keep = False

    if keep:
      yield {
        'text': text
      }

    if text.startswith(SKIP_END):
      keep = True


def parse_offsets(lines):
  all_offsets = set()

  def inner(lines):
    lines2 = []
    for line in lines:
      offset, text = rx_offset_line.findall(line['text'])[0]
      offset = int(offset)

      all_offsets.add(offset)

      yield {
        **line,
        'offset': offset,
        'text': text,
      }

  lines = list(inner(lines))

  all_offsets = { x: i + 1 for i, x in enumerate(sorted(all_offsets)) }

  for line in lines:
    yield {
      **line,
      'offset': all_offsets[line['offset']]
    }


def remove_tags(lines):
  for line in lines:
    text = line['text']
    text = text.replace('<b>Пояснення: </b>', 'Пояснення: ')
    text = text.replace('<b>“</b>', '“')
    text = text.replace('<b>"</b>', '"')
    text = text.replace('</b>?', '?</b>')
    text = text.replace('<b>“ </b>', '“ ')
    text = text.replace('<font style="font-size:14px">', '')
    text = text.replace('</font>', '')
    text = text.replace('<br>', '')
    text = text.replace('</p>', '')
    text = text.replace('<p>', '')
    if text.startswith('<b>') and text.endswith('</b>'):
      text = text[3:-4]

    yield {
      **line,
      'text': text,
    }


def pre_normalize_typography(text):
  text = text.replace('“', '"')
  text = text.replace('”', '"')
  text = text.replace('«', '"')
  text = text.replace('»', '"')
  text = text.replace('–', '-')
  text = text.replace('—', '-')
  text = text.strip()
  text = text.strip('.')
  text = re.sub(r'\s+', ' ', text)
  
  return text


def post_normalize_typography(text):
  text = text.replace(' - ', ' — ')
  text = text.replace('-', '–')
  text = re.sub(r'"(.*?)"', r'«\1»', text)
  
  # uppercase first letter
  text = re.sub(r'([^\W\d_])', lambda x: x[1].upper(), text, 1)
  
  return text


def normalize_typography(text):
  return post_normalize_typography(pre_normalize_typography(text))


def join_block(blocks):
  assert blocks
  blocks = list(blocks)

  parts = []
  for block in blocks[:-1]:
    if block.endswith('-') and not block.endswith(' -'):
      parts.append(block)
    else:
      parts.append(block)
      parts.append(' ')
  parts.append(blocks[-1])

  return ''.join(parts)


def split_questions_q1(lines):
  block = []
  for line in lines:
    if rx_question_start.match(line['text']):
      if block:
        yield block
        block = []

    block.append(line)

  if block:
    yield block


def split_questions_q2(lines):
  block = []
  for line in lines:
    if line['text'].startswith(T_QUESTION):
      if block:
        yield block
        block = []

    block.append(line)

  if block:
    yield block


def parse_question_q1(lines):
  answers = []
  wait_for = 'q'

  for line in lines:
    text = line['text']

    if wait_for == 'q':
      rxm = rx_question_start.match(text)
      assert rxm
      question, = rxm.groups()
      question = normalize_typography(question)
      wait_for = 'a'
    elif wait_for == 'a':
      correct_flag, answer_text = text[:1], text[1:]
      assert correct_flag in '+-'
      answer_text = normalize_typography(answer_text)

      answers.append({
        'text': answer_text,
        'correct': correct_flag == '+',
      })

  return {
    'question': question,
    'answers': answers,
  }


def parse_question_q2(lines):
  question_block = []
  answers_block = []
  wait_for = None

  for line in lines:
    text = line['text']

    if text.startswith(T_EXPLANATION):
      break

    if wait_for is None:
      if text.startswith(T_QUESTION):
        wait_for = 'q'
    elif wait_for == 'q':
      if text.startswith(T_ANSWERS):
        wait_for = 'a'
      else:
        question_block.append(line)
    elif wait_for == 'a':
      answers_block.append(line)

  question = parse_question_block(question_block)
  answers = parse_answers_block(answers_block)

  return {
    'question': question,
    'answers': answers,
  }


def parse_question_block(lines):
  question = post_normalize_typography(join_block(pre_normalize_typography(line['text']) for line in lines))

  if question.startswith('1. '):
    question = question[3:]

  return question


def parse_answers_block(lines):
  blocks = {}

  for line in lines:
    blocks.setdefault(line['offset'], []).append(line['text'])

  keys = sorted(blocks.keys())
  options_block = blocks.pop(min(keys))

  # remove "correct answer" header
  for k in list(blocks.keys()):
    if blocks[k] == ['Правильна', 'відповідь']:
      del blocks[k]
    elif blocks[k] == ['Правильна', 'відповідь:']:
      del blocks[k]

  # parse correct answer
  correct_block = []
  while blocks:
    correct_block.extend(blocks.pop(max(blocks)))

  correct = parse_correct_block(correct_block)
  options = parse_options_block(options_block)

  answers = []
  for x in options:
    opt_seq = x['seq']
    opt_text = x['text']

    answers.append({
      'text': opt_text,
      'correct': correct['seq'] == opt_seq,
    })
  
  return answers


def parse_correct_block(lines):
  correct = post_normalize_typography(join_block(pre_normalize_typography(line) for line in lines))
  rxm = rx_option_start.match(correct)
  if not rxm:
    raise ValueError('correct entry has no sequence')

  seq, text = rxm.groups()
  seq = int(seq)
  text = normalize_typography(text)

  return {
    'seq': seq,
    'text': text,
  }


def parse_options_block(lines):
  options = []
  option = {
    'seq': None,
    'block': [],
  }

  def push():
    if option['seq'] is not None:
      options.append({
        'seq': option['seq'],
        'text': post_normalize_typography(join_block(pre_normalize_typography(line) for line in option['block'])),
      })
      option['seq'] = None
      option['block'] = []

  for text in lines:
    rxm = rx_option_start.match(text)
    if rxm:
      push()

      opt_seq, opt_text = rxm.groups()
      opt_seq = int(opt_seq)
      opt_text = normalize_typography(opt_text)
      option['seq'] = opt_seq
      option['block'].append(opt_text)
    else:
      option['block'].append(text)

  push()

  return options   


def transform_q1():
  questions = [parse_question_q1(question) for question in split_questions_q1(remove_tags(read('q1.html')))]

  return questions


def transform_q2():
  questions = [parse_question_q2(question) for question in split_questions_q2(remove_tags(parse_offsets(skip_page_numbers(read('q2.html')))))]

  return questions


def minimize(xs):
  result = []

  for q in xs:
    entry = {}
    entry['q'] = q['question']
    entry['a'] = []
    for i, a in enumerate(q['answers']):
      entry['a'].append(a['text'])
      if a['correct']:
        c = i
    entry['c'] = c

    result.append(entry)

  result.sort(key = lambda x: repr(x))

  return result


q = minimize([
  *transform_q1(),
  *transform_q2(),
])

with open('questions.js', 'w', encoding='utf8') as fp:
  fp.write('addQuestions(')
  fp.write(json.dumps(q, ensure_ascii=False, separators=(',', ':')))
  fp.write(');')
