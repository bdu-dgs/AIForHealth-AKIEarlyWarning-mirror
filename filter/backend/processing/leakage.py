HIGH_RISK={'DISCHTIME','DEATHTIME','DISCHARGE_LOCATION','HOSPITAL_EXPIRE_FLAG'}

def check(events,prediction,columns,assignment=None):
    findings=[]
    if any(c.upper() in HIGH_RISK for c in columns): findings.append('Outcome-bearing feature field')
    if len(events) and ((events.CHARTTIME>prediction)|(events.AVAILABLETIME>prediction)).any(): findings.append('Future measurement or future availability in features')
    if assignment is not None and (assignment.groupby('SUBJECT_ID')['split'].nunique()>1).any(): findings.append('Patient occurs in multiple splits')
    return {'status':'FAIL' if findings else 'PASS','findings':findings}
