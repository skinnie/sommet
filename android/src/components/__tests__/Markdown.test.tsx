import React from 'react';
// react-test-renderer ships no types in this project.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const renderer = require('react-test-renderer');
const { act } = renderer;
import { Text } from 'react-native';
import Markdown, { parseMarkdown, parseInline } from '../Markdown';

describe('parseInline', () => {
  it('reads bold, italic, code and links', () => {
    expect(parseInline('an **easy** day, *really*, `z2` and [intervals](https://intervals.icu)')).toEqual([
      { text: 'an ' },
      { text: 'easy', bold: true },
      { text: ' day, ' },
      { text: 'really', italic: true },
      { text: ', ' },
      { text: 'z2', code: true },
      { text: ' and ' },
      { text: 'intervals', link: 'https://intervals.icu' },
    ]);
  });
  it('handles bold inside italic and the underscore forms', () => {
    expect(parseInline('*very **hard** day*')).toEqual([
      { text: 'very ', italic: true },
      { text: 'hard', italic: true, bold: true },
      { text: ' day', italic: true },
    ]);
    expect(parseInline('__bold__ and _soft_')).toEqual([
      { text: 'bold', bold: true }, { text: ' and ' }, { text: 'soft', italic: true },
    ]);
  });
  it('leaves things that are not Markdown alone', () => {
    expect(parseInline('2 * 3 = 6, snake_case_word, a lone ** here')).toEqual([
      { text: '2 * 3 = 6, snake_case_word, a lone ** here' },
    ]);
    expect(parseInline('\\*not italic\\*')).toEqual([{ text: '*not italic*' }]);
    expect(parseInline('[no scheme](javascript:alert)')).toEqual([{ text: '[no scheme](javascript:alert)' }]);
  });
});

describe('parseMarkdown', () => {
  it('splits paragraphs, lists and headings', () => {
    const blocks = parseMarkdown('# Today\nYou look\nfresh.\n\n- one\n- **two**\n  more\n\n1. first\n2. second\n\nDone.');
    expect(blocks.map(b => b.kind)).toEqual(['heading', 'paragraph', 'bullet', 'ordered', 'paragraph']);
    expect(blocks[1]).toEqual({ kind: 'paragraph', spans: [{ text: 'You look fresh.' }] });
    expect(blocks[2]).toEqual({ kind: 'bullet', items: [[{ text: 'one' }], [{ text: 'two', bold: true }, { text: ' more' }]] });
    expect(blocks[3]).toMatchObject({ kind: 'ordered', start: 1 });
  });
  it('starts a numbered list at its own number', () => {
    expect(parseMarkdown('3) three\n4) four')[0]).toMatchObject({ kind: 'ordered', start: 3 });
  });
});

describe('<Markdown>', () => {
  it('gives every Text a fontSize (an inline Text without one is invisible on BlissOS)', () => {
    let tree: any;
    act(() => {
      tree = renderer.create(
        <Markdown text={'**Hi** *there*\n\n- a [link](https://example.com)\n1. `x`'} color="#000" linkColor="#0a0" fontSize={13} />,
      );
    });
    const texts: any[] = tree.root.findAllByType(Text);
    expect(texts.length).toBeGreaterThan(5);
    for (const t of texts) {
      const style = Array.isArray(t.props.style) ? Object.assign({}, ...t.props.style) : t.props.style;
      expect(style?.fontSize).toBe(13);
    }
  });
});
