#
# simpleBool.py
#
# Example of defining a boolean logic parser using
# the operatorGrammar helper method in pyparsing.
#
# In this example, parse actions associated with each
# operator expression will "compile" the expression
# into BoolXXX class instances, which can then
# later be evaluated for their boolean value.
#
# Copyright 2006, by Paul McGuire
# Updated 2013-Sep-14 - improved Python 2/3 cross-compatibility
#
class BoolParser(object):
    from pyparsing import infixNotation, opAssoc, Keyword, Word, alphas, ParserElement, Regex

    # define classes to be built at parse time, as each matching
    # expression type is parsed
    class BoolOperand(object):
        def __init__(self, t, eval_func=eval):
            self.label = t[0]
            self.func = eval_func
        def __bool__(self):
            return self.func(self.label)
        def __str__(self):
            return self.label
        __repr__ = __str__
        __nonzero__ = __bool__

    class BoolBinOp(object):
        def __init__(self,t):
            self.args = t[0][0::2]
        def __str__(self):
            sep = " %s " % self.reprsymbol
            return "(" + sep.join(map(str,self.args)) + ")"
        def __bool__(self):
            return self.evalop(bool(a) for a in self.args)
        __nonzero__ = __bool__
        __repr__ = __str__

    class BoolAnd(BoolBinOp):
        reprsymbol = '&'
        evalop = all

    class BoolOr(BoolBinOp):
        reprsymbol = '|'
        evalop = any

    class BoolNot(object):
        def __init__(self,t):
            self.arg = t[0][1]
        def __bool__(self):
            v = bool(self.arg)
            return not v
        def __str__(self):
            return "~" + str(self.arg)
        __repr__ = __str__
        __nonzero__ = __bool__

    def _eval(s, *args):
        return BoolParser.BoolOperand(*(args + (s.eval_func,)))

    def __init__(s, eval_func=eval):
        s.eval_func = eval_func
        #TRUE = BoolParser.Keyword("True")
        #FALSE = BoolParser.Keyword("False")
        #s.boolOperand = TRUE | FALSE | BoolParser.Word(BoolParser.alphas,max=1)
        #s.boolOperand = BoolParser.Word(BoolParser.alphas,max=1)
        s.boolOperand = BoolParser.Regex('[\w>=0-9]+')
        s.boolOperand.setParseAction(s._eval)
        #BoolParser.ParserElement.enablePackrat()

        # define expression, based on expression operand and
        # list of operations in precedence order
        s.boolExpr = BoolParser.infixNotation(s.boolOperand,
            [
            ("not", 1, BoolParser.opAssoc.RIGHT, BoolParser.BoolNot),
            ("and", 2, BoolParser.opAssoc.LEFT,  BoolParser.BoolAnd),
            ("or",  2, BoolParser.opAssoc.LEFT,  BoolParser.BoolOr),
            ])

    def parseString(s, string, print_repr=False):
        return s.boolExpr.parseString(string)
