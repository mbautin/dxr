#!/usr/bin/env python2.6

import sys
import os
import re
import sqlite3
import template
import cgi
from tokenizers import Token, BaseTokenizer, CppTokenizer

class HtmlBuilderBase:
  def collectSidebar(self):
    """Returns a list of (name, line, title, img, container) for items that
    belong in the sidebar."""
    return []

  def getSyntaxRegions(self):
    """Returns a list of (start, end+1, kind) tokens for syntax highlighting."""
    return []

  def getLinkRegions(self):
    """Returns a list of (start, end+1, {attr:val}) tokens for links."""
    return []

  def getLineAnnotations(self):
    return []

  def _init_db(self, database):
    self.conn = sqlite3.connect(database)
    self.conn.execute('PRAGMA temp_store = MEMORY;')

  def __init__(self, tree, filepath, dstpath):
    # Read and expand all templates
    self.html_header = tree.getTemplateFile("dxr-header.html")
    self.html_footer = tree.getTemplateFile("dxr-footer.html")
    self.html_sidebar_header = tree.getTemplateFile("dxr-sidebar-header.html")
    self.html_sidebar_footer = tree.getTemplateFile("dxr-sidebar-footer.html")
    self.html_main_header = tree.getTemplateFile("dxr-main-header.html")
    self.html_main_footer = tree.getTemplateFile("dxr-main-footer.html")
    
    self.source = template.readFile(filepath)
    self.virtroot = tree.virtroot
    self.treename = tree.tree
    self.filename = os.path.basename(filepath)
    self.srcroot = tree.sourcedir
    self.dstpath = os.path.normpath(dstpath)
    self.srcpath = filepath.replace(self.srcroot + '/', '')

    self._init_db(tree.database)
    self.tokenizer = self._createTokenizer()

    # Config info used by dxr.js
    self.globalScript = ['var virtroot = "%s", tree = "%s";' % (self.virtroot, self.treename)]

  def _createTokenizer(self):
    return BaseTokenizer(self.source)

  def _buildFullPath(self, ending, includeTreename=True):
    if includeTreename:
      return os.path.join(self.virtroot, self.treename, ending) 
    else:
      return os.path.join(self.virtroot, ending) 

  def escapeString(self, token, line, line_start, offset, prefix='', suffix=''):
    start = token.start - line_start + offset
    end = token.end - line_start + offset
    escaped = prefix + cgi.escape(token.name) + suffix
    # token is (perhaps) different size, so update offset with new width
    offset += len(escaped) - len(token.name)
    line = line[:start] + escaped + line[end:]

    return (offset, line)

  def toHTML(self):
    out = open(self.dstpath, 'w')
    out.write(self.html_header + '\n')
    self.writeSidebar(out)
    self.writeMainContent(out)
    self.writeGlobalScript(out)
    out.write(self.html_footer + '\n')
    out.close()

  def writeSidebar(self, out):
    sidebarElements = [x for x in self.collectSidebar()]
    if len(sidebarElements) == 0: return

    out.write(self.html_sidebar_header + '\n')
    self.writeSidebarBody(out, sidebarElements)
    out.write(self.html_sidebar_footer + '\n')

  def writeSidebarBody(self, out, elements):
    containers = {}
    for e in elements:
      containers.setdefault(len(e) > 4 and e[4] or None, []).append(e)

    # Sort the containers by their location
    # Global scope goes last, and scopes declared outside of this file goes
    # before everything else
    clocs = { None: 2 ** 32 }
    for e in elements:
      if e[0] in containers:
        clocs[e[0]] = int(e[1])
    contKeys = containers.keys()
    contKeys.sort(lambda x, y: cmp(clocs.get(x, 0), clocs.get(y, 0)))

    for cont in contKeys:
      if cont is not None:
        out.write('<b>%s</b>\n<div>\n' % cgi.escape(str(cont)))
      containers[cont].sort(lambda x, y: int(x[1]) - int(y[1]))
      for e in containers[cont]:
        img = len(e) > 3 and e[3] or "images/icons/page_white_code.png"
        title = len(e) > 2 and e[2] or e[0]
        img = self._buildFullPath(img)
        out.write('<img src="%s/%s" class="sidebarimage">' % (self.virtroot, img))
        out.write('<a class="sidebarlink" title="%s" href="#l%d">%s</a><br>\n' %
          (cgi.escape(title), int(e[1]), cgi.escape(e[0])))
      if cont is not None:
        out.write('</div><br />\n')

  def writeMainContent(self, out):
    out.write(self.html_main_header)
    self.writeMainBody(out)
    out.write(self.html_main_footer)

  def writeMainBody(self, out):
    syntax_regions = self.getSyntaxRegions()
    links = self.getLinkRegions()
    lines = self.getLineAnnotations()

    # Split up the entire source, and annotate each char invidually
    line_markers = [0]
    closure = ['', 0]
    def handle_char(x):
      if x == '\n':
        line_markers.append(closure[1])
      elif closure[0] == '\r':
        line_markers.append(closure[1] - 1)
      closure[0] = x
      closure[1] += 1
      if x == '\r' or x == '\n': return ''
      return cgi.escape(x)
    chars = [handle_char(x) for x in self.source]
    chars.append('')

    def off(val):
      if isinstance(val, tuple):
        return line_markers[val[0] - 1] + val[1]
      return val
    for syn in syntax_regions:
      chars[off(syn[0])] = '<span class="%s">%s' % (syn[2], chars[off(syn[0])])
      chars[off(syn[1]) - 1] += '</span>'
    for link in links:
      chars[off(link[0])] = '<a aria-haspopup="true" %s>%s' % (
        ' '.join([attr + '="' + str(link[2][attr]) + '"' for attr in link[2]]),
        chars[off(link[0])])
      chars[off(link[1]) - 1] += '</a>'

    # the hack is that we need the first and end to work better
    # The last "char" is the place holder for the first line entry
    line_markers[0] = -1
    # Line attributes
    for l in lines:
      chars[line_markers[l[0] - 1]] = \
        ' '.join([attr + '="' + str(l[1][attr]) + '"' for attr in l[1]])
    line_num = 2 # First line is special
    for ind in line_markers[1:]:
      chars[ind] = '</div><div %s id="l%d"><a class="ln" href="l%d">%d</a>' % \
        (chars[ind], line_num, line_num, line_num)
      line_num += 1
    out.write('<div %s id="l1"><a class="ln" href="l1">1</a>' % chars[-1])
    chars[-1] = '</div>'
    out.write(''.join(chars))

  def writeGlobalScript(self, out):
    """ Write any extra JS for the page. Lines of script are stored in self.globalScript."""
    # Add app config info
    out.write('<script type="text/javascript">')
    out.write('\n'.join(self.globalScript))
    out.write('</script>')


class CppHtmlBuilder(HtmlBuilderBase):
  def __init__(self, treeconfig, filepath, dstpath, blob):
    HtmlBuilderBase.__init__(self, treeconfig, filepath, dstpath)
    self.syntax_regions = None
    self.blob_file = blob["byfile"].get(self.srcpath, None)
    self.blob = blob

  def _createTokenizer(self):
    return CppTokenizer(self.source)

  def collectSidebar(self):
    if self.blob_file is None:
      return
    lst = []
    def line(linestr):
      return linestr.split(':')[1]
    def make_tuple(df, name, loc, scope="scopeid"):
      img = 'images/icons/page_white_wrench.png'
      if scope in df and df[scope] > 0:
        return (df[name], df[loc].split(':')[1], df[name], img,
          self.blob["scopes"][df[scope]]["sname"])
      return (df[name], df[loc].split(':')[1], df[name], img)
    for df in self.blob_file["types"]:
      yield make_tuple(df, "tqualname", "tloc", "scopeid")
    for df in self.blob_file["functions"]:
      yield make_tuple(df, "flongname", "floc", "scopeid")
    for df in self.blob_file["variables"]:
      if "scopeid" in df and df["scopeid"] in self.blob["functions"]:
        continue
      yield make_tuple(df, "vname", "vloc", "scopeid")

  def _getFromTokenizer(self):
    syntax_regions = []
    for token in self.tokenizer.getTokens():
      if token.token_type == self.tokenizer.KEYWORD:
        syntax_regions.append((token.start, token.end, 'k'))
      elif token.token_type == self.tokenizer.STRING:
        syntax_regions.append((token.start, token.end, 'str'))
      elif token.token_type == self.tokenizer.COMMENT:
        syntax_regions.append((token.start, token.end, 'c'))
      elif token.token_type == self.tokenizer.PREPROCESSOR:
        syntax_regions.append((token.start, token.end, 'p'))
    self.syntax_regions = syntax_regions

  def getSyntaxRegions(self):
    if self.syntax_regions is None:
      self._getFromTokenizer()
    return self.syntax_regions

  def getLinkRegions(self):
    if self.blob_file is None:
      return
    def make_link(obj, loc, name, clazz, **kwargs):
      line, col = obj[loc].split(':')[1:]
      line, col = int(line), int(col)
      kwargs['class'] = clazz
      kwargs['line'] =  line
      return ((line, col), (line, col + len(obj[name])), kwargs)
    for df in self.blob_file["variables"]:
      yield make_link(df, 'vloc', 'vname', 'var', rid=df['varid'])
    for df in self.blob_file["functions"]:
      yield make_link(df, 'floc', 'fname', 'func', rid=df['funcid'])
    for df in self.blob_file["types"]:
      yield make_link(df, 'tloc', 'tqualname', 't', rid=df['tid'])
    for df in self.blob_file["refs"]:
      start, end = df["extent"].split(':')
      yield (int(start), int(end), {'class': 'ref', 'rid': df['refid']})

  def getLineAnnotations(self):
    if self.blob_file is None:
      return
    for warn in self.blob_file["warnings"]:
      line = int(warn["wloc"].split(":")[1])
      yield (line, {"class": "lnw", "title": warn["wmsg"]})