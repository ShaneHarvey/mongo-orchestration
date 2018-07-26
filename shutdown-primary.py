import sys
from pymongo import errors, MongoClient

port = int(sys.argv[1])
client = MongoClient(port=port, serverSelectionTimeoutMS=10000)
try:
    res = client.admin.command('shutdown', force=1)
    print(res)
except errors.ConnectionFailure as exc:
    print(exc)
except errors.PyMongoError as exc:
    print(exc)
    raise
print('\n*******exiting')
