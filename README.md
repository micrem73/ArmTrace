# **ArmTrace — Data Pipeline**

This folder contains two scripts for extracting and structuring military export data from Italian government annual reports published under **Law 185/1990**.



## **Scripts**

### **2024relations2IndividualTables**

Parses official Law 185/1990 annual report PDFs. The script automatically locates the table of contents, extracts table names and page references, and generates a separate PDF file for each table.

**Status:** Tested and working with 2024 reports.



### **IndividualTables2SQL**

Reads the single-table PDFs produced by the previous step, extracts structured data, and loads it into a SQL database. Also supports export to Excel.

**Status:** Working for a subset of table types. Broader table support is under active development.



## **Usage**

Run the scripts in order:

1. 2024relations2IndividualTables   →   produces one PDF per table

2. IndividualTables2SQL             →   loads tables into SQL / Excel



## **ToDo**

- Iterate on all reports 

- Populate a MySQL db

- create a LLM tool that convert natural language requests into SQL queries

- create a chatbot enhanced with the tool, allowing to interrogate the DB in natural language

- participatory workshop for the design of the interface

- test, test, test



## **Notes**

- Input data is sourced from publicly available government reports under Italian Law 185/1990 (annual reports on military exports).

- This pipeline is the technical foundation of the ArmTrace civic transparency platform.
