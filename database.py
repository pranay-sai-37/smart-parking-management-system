
import mysql.connector
from mysql.connector import Error


def get_connection():
    try:
        connection = mysql.connector.connect(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="",
            database="smart_parking_db"
        )
        return connection

    except Error as e:
        print("MySQL connection error:", e)
        return None
